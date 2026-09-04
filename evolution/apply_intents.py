#!/usr/bin/env python3
"""Consomme les intentions de pilotage au lancement d'un run
(SPEC.md §4.3) — appelé par run_night.sh.

Traduction des verbes en ordres permanents :
- pin <programme>     -> exemplaire de référence cité dans le prompt ;
- cut <programme>     -> direction déclarée impasse dans le prompt ;
- explore <cx,cy>     -> zone comportementale cible dans le prompt ;
- branch <programme>  -> LE RUN DÉMARRE DE CE PROGRAMME (graine), la
                         note d'intention devient une directive.

Écrit <out>/config.yaml (system_message enrichi) et <out>/initial.js
(la graine), puis marque les intentions consommées avec l'id du run —
sauf --dry-run. Sans intention en attente, copie config et graine à
l'identique : le chemin de lancement est le même avec ou sans ordres.

Usage : apply_intents.py <dossier_du_run> [--dry-run]
"""

import os
import shutil
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "harness"))
from oadlab import db  # noqa: E402

HEADER = ("\nOPERATOR GUIDANCE — standing orders from the human "
          "operator. They override the default exploration "
          "priorities; ignoring them wastes the run:\n")


def _block_str(dumper, data):
    """system_message en style bloc « | » : le config.yaml effectif
    doit rester lisible et diffable (il est haché par le manifeste)."""
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data,
                                   style=style)


yaml.SafeDumper.add_representer(str, _block_str)

NOTEBOOK_HEADER = ("\nLAB NOTEBOOK — lessons distilled from prior "
                   "experiments in this lab. Build on the laws, do not "
                   "retry the impasses, the open questions are worthy "
                   "experiments:\n")
KIND_LABEL = {"law": "LAW", "impasse": "IMPASSE", "question": "OPEN"}


def notebook(con):
    """Le carnet actif, formaté pour le system_message. Désactivable
    (OADLAB_NO_NOTEBOOK=1) pour les nuits A/B : l'étage cognitif doit
    prouver qu'il sert (SPEC.md §3 bis)."""
    if os.environ.get("OADLAB_NO_NOTEBOOK"):
        return []
    return [f"- [{KIND_LABEL.get(r['kind'], r['kind'])}] {r['statement']}"
            f"{f' ({r['confidence']})' if r['confidence'] else ''}"
            for r in db.active_lessons(con)]


def _program(con, pid):
    row = con.execute(
        "SELECT id, hypothesis, combined_score, code FROM programs"
        " WHERE id = ?", (pid,)).fetchone()
    return dict(row) if row else None


def guidance(con, intents, seed_id, grid):
    """`seed_id` est le programme RÉELLEMENT retenu comme graine (ou
    None) : la guidance ne doit jamais affirmer qu'un branch perdant
    ou introuvable est le point de départ du run."""
    lines = []
    for it in intents:
        verb, target, note = it["verb"], it["target"] or "", it["note"]
        p = _program(con, target) if verb in ("pin", "cut", "branch") \
            else None
        short = target[:8]
        hyp = (p or {}).get("hypothesis") or "no recorded hypothesis"
        score = (p or {}).get("combined_score")
        if verb == "pin":
            lines.append(
                f"- PINNED exemplar {short}"
                f"{f' (score {score:.3f})' if score is not None else ''}:"
                f" \"{hyp}\". The operator marked this approach as"
                " valuable — preserve and build on what makes it strong.")
        elif verb == "cut":
            lines.append(
                f"- CUT {short}: the approach \"{hyp}\" is a dead end"
                " per the operator — do not pursue similar directions.")
        elif verb == "explore":
            try:
                cx, cy = (int(x) for x in target.split(","))
            except ValueError:
                continue
            zone = (f"aggression in [{cx / grid:.2f}, {(cx + 1) / grid:.2f})"
                    f" and boom in [{cy / grid:.2f}, {(cy + 1) / grid:.2f})")
            extra = f" Operator note: \"{note}\"." if note else ""
            lines.append(
                f"- EXPLORE: aim mutations at the behavior zone {zone} —"
                " landing there is valuable even at equal score." + extra)
        elif verb == "branch":
            extra = f" Operator intent: \"{note}\"." if note else ""
            if target == seed_id:
                lines.append(
                    f"- BRANCH: this run's starting program IS {short}"
                    f" (\"{hyp}\").{extra} Explore variations serving"
                    " that intent.")
            else:
                lines.append(
                    f"- BRANCH requested toward {short} but it is NOT"
                    " this run's seed (superseded or unresolved) —"
                    f" treat its intent as secondary guidance.{extra}")
    return lines


def main():
    out = Path(sys.argv[1]).resolve()
    dry = "--dry-run" in sys.argv[2:]
    out.mkdir(parents=True, exist_ok=True)
    con = db.connect()
    intents = [dict(r) for r in db.pending_intents(con)]
    cfg = yaml.safe_load((REPO / "evolution/config.yaml").read_text())

    # La graine est décidée AVANT la guidance : elle ne doit jamais
    # affirmer qu'un branch perdant est le point de départ.
    seed_prog, seed_note = None, ""
    branches = [i for i in intents if i["verb"] == "branch"]
    if branches:
        seed_prog = _program(con, branches[-1]["target"])
        if not (seed_prog and seed_prog.get("code")):
            print(f"branch {branches[-1]['target'][:8]} : programme"
                  " introuvable, graine baseline", file=sys.stderr)
            seed_prog = None
        elif "export function Strategy" not in seed_prog["code"]:
            # Programme d'avant le chantier D : son code est un
            # config.js, l'installer comme strategy.js casserait le
            # bot. L'ancien substrat n'est pas branchable.
            print(f"branch {seed_prog['id'][:8]} : substrat config.js"
                  " (pré-chantier D), non branchable — graine baseline",
                  file=sys.stderr)
            seed_prog = None
    if seed_prog:
        (out / "initial.js").write_text(seed_prog["code"])
        seed_note = f", graine {seed_prog['id'][:8]}"
    else:
        shutil.copyfile(REPO / "evolution/initial_strategy.js",
                        out / "initial.js")

    grid = cfg.get("database", {}).get("feature_bins", 8)
    notes = notebook(con)
    if notes:
        cfg["prompt"]["system_message"] += \
            NOTEBOOK_HEADER + "\n".join(notes) + "\n"
    lines = guidance(con, intents,
                     seed_prog["id"] if seed_prog else None, grid)
    if lines:
        cfg["prompt"]["system_message"] += HEADER + "\n".join(lines) + "\n"
    (out / "config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))

    if not dry:
        for it in intents:
            db.consume_intent(con, it["id"], out.name)
    con.close()
    verbs = ", ".join(f"{i['verb']}:{(i['target'] or '')[:8]}"
                      for i in intents) or "aucune"
    print(f"intentions consommées ({'dry-run, ' if dry else ''}"
          f"{len(intents)}) : {verbs}{seed_note} ;"
          f" carnet : {len(notes)} leçon(s)")


if __name__ == "__main__":
    main()
