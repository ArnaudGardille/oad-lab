#!/usr/bin/env python3
"""Importe un run d'évolution en base : ligne `runs` + film `events`.

Usage : scripts/import_run.py runs/evolution/<run> [...]

Idempotent (INSERT OR REPLACE sur le run, DELETE+INSERT des events) :
réimporter un run en cours met son film à jour — c'est le chemin
normal du front pour suivre un run live (P1 : le front ne lit jamais
le log, il lit la base).

La provenance vient du manifeste run.json écrit par run_night.sh au
lancement ; les runs antérieurs au manifeste sont importés avec une
provenance nulle. Les événements viennent de night.log (formats
observés sur la nuit du 2026-09-02).
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
from oadlab import db  # noqa: E402

TS = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})"
PATTERNS = [
    # (kind, regex, groupes -> iteration / program_id / payload)
    ("iteration", re.compile(
        TS + r" .*Iteration (\d+): Program ([0-9a-f-]+) "
        r"\(parent: ([0-9a-f-]+)\) completed in ([\d.]+)s")),
    ("evaluated", re.compile(
        TS + r" .*Evaluated program ([0-9a-f-]+) in [\d.]+s: (.+)")),
    ("new_best", re.compile(
        TS + r" .*New best program ([0-9a-f-]+) replaces ([0-9a-f-]+) "
        r"\(combined_score: ([\d.]+) → ([\d.]+)")),
    ("cell_occupied", re.compile(
        TS + r" .*New MAP-Elites cell occupied in island (\d+)(.*)")),
    ("checkpoint", re.compile(
        TS + r" .*Saved checkpoint at iteration (\d+)")),
    ("error", re.compile(TS + r" - WARNING - Iteration (\d+) error: (.+)")),
    # Deux lignes de fin distinctes dans le log : les métriques finales
    # d'abord (plus spécifique), la clôture ensuite.
    ("final_metrics", re.compile(
        TS + r" .*Evolution complete\. Best program has metrics: (.+)")),
    ("completed", re.compile(TS + r" .*Evolution completed[ -]*(.*)")),
]


def _ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S,%f").timestamp()


def parse_log(text):
    events = []
    for line in text.splitlines():
        for kind, rx in PATTERNS:
            m = rx.search(line)
            if not m:
                continue
            g = m.groups()
            e = {"ts": _ts(g[0]), "kind": kind,
                 "iteration": None, "program_id": None, "payload": None}
            if kind == "iteration":
                e.update(iteration=int(g[1]), program_id=g[2],
                         payload=json.dumps({"parent": g[3],
                                             "seconds": float(g[4])}))
            elif kind == "evaluated":
                e.update(program_id=g[1], payload=json.dumps(
                    {"metrics": g[2].strip()}))
            elif kind == "new_best":
                e.update(program_id=g[1], payload=json.dumps(
                    {"replaces": g[2], "from": float(g[3]),
                     "to": float(g[4])}))
            elif kind == "cell_occupied":
                e.update(payload=json.dumps(
                    {"island": int(g[1]), "detail": g[2].strip()}))
            elif kind in ("checkpoint", "error"):
                e.update(iteration=int(g[1]))
                if kind == "error":
                    e.update(payload=json.dumps({"message": g[2].strip()}))
            elif kind == "final_metrics":
                e.update(payload=json.dumps({"metrics": g[1].strip()}))
            elif kind == "completed":
                e.update(payload=json.dumps({"detail": g[1].strip()}))
            events.append(e)
            break
    return events


def import_run(con, run_dir):
    run_dir = Path(run_dir).resolve()
    run_id = run_dir.name
    log = run_dir / "night.log"
    if not log.exists():
        sys.exit(f"pas de night.log dans {run_dir}")
    events = parse_log(log.read_text(errors="replace"))
    db.replace_events(con, run_id, events)

    manifest = {}
    mpath = run_dir / "run.json"
    if mpath.exists():
        try:
            manifest = json.loads(mpath.read_text())
        except json.JSONDecodeError:
            print(f"  run.json illisible, provenance ignorée ({run_id})")

    iters = [e["iteration"] for e in events
             if e["kind"] == "iteration" and e["iteration"] is not None]
    completed = any(e["kind"] in ("completed", "final_metrics")
                    for e in events)
    # started : le manifeste (écrit au lancement) fait foi — le premier
    # événement du log arrive ~2 min après (setup + 1re génération).
    # Sans manifeste (runs anciens), marge de 10 min vers l'arrière
    # pour la fenêtre de comptage : les parties de la première éval
    # sont jouées AVANT la première ligne "Evaluated" du log.
    started = manifest.get("started")
    margin = 0 if started else 600
    if not started:
        started = min((e["ts"] for e in events), default=None)
    last_ts = max((e["ts"] for e in events), default=None)
    if completed:
        status = "completed"
    elif last_ts and time.time() - log.stat().st_mtime < 1800:
        status = "running"
    else:
        # Ni terminé ni de vie depuis 30 min : le process est mort.
        status = "crashed"

    # games : compté depuis matches (la vérité, une ligne par partie
    # jouée) sur la fenêtre temporelle du run. Le log sous-compte : le
    # merge de cascade d'OpenEvolve écrase le `games` du stage 1 par
    # celui du stage 2 avant de logger (revue 2026-09-03). Hypothèse :
    # jamais deux campagnes en même temps (garde-fou machine).
    games = con.execute(
        "SELECT count(*) FROM matches WHERE ts BETWEEN ? AND ?",
        ((started or 0) - margin, (last_ts or 0) + 60)).fetchone()[0]

    db.upsert_run(
        con, id=run_id, kind="evolution",
        started=started,
        finished=last_ts if completed else None,
        git_commit=manifest.get("git_commit"),
        dirty=manifest.get("dirty"),
        config_sha=manifest.get("config_sha"),
        hof=manifest.get("hof"),
        iterations=manifest.get("iterations"),
        iterations_done=max(iters, default=0),
        errors=sum(1 for e in events if e["kind"] == "error"),
        games=games,
        status=status)
    print(f"{run_id}: {len(events)} événements, "
          f"{max(iters, default=0)} itérations, {games} parties, "
          f"statut {status}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    con = db.connect()
    for run_dir in sys.argv[1:]:
        import_run(con, run_dir)
    con.close()


if __name__ == "__main__":
    main()
