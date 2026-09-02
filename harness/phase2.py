#!/usr/bin/env python3
"""Phase 2 : validation du fork de Petra.

Joue N seeds, chacune deux fois avec les positions échangées
(forkbot/petra puis petra/forkbot), en vagues parallèles d'une partie
par cœur physique. Le fork étant un clone de Petra, son winrate global
doit tourner autour de 50%.

L'appariement replay↔partie se fait via (seed, IA du joueur 1) lus dans
l'en-tête "start" de commands.txt.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

REPLAYS = Path.home() / "snap/0ad/current/.local/share/0ad/replays/0.28.0"
OUT = Path(__file__).resolve().parent.parent / "runs/phase2"


def cmd_for(seed, ai1, ai2):
    return [
        "0ad", "--mod=public", "--mod=oadlab",
        "--autostart=random/mainland", "--autostart-size=128",
        f"--autostart-seed={seed}",
        f"--autostart-ai=1:{ai1}", f"--autostart-ai=2:{ai2}",
        "--autostart-nonvisual",
    ]


def parse_replay(d):
    commands = d / "commands.txt"
    metadata = d / "metadata.json"
    if not commands.exists() or not metadata.exists():
        return None
    header = commands.read_text(errors="replace").split("\n", 1)[0]
    m = re.search(r'"Seed":\s*(\d+)', header)
    ais = re.findall(r'"AI":\s*"([^"]*)"', header)
    meta = json.loads(metadata.read_text())
    states = [p.get("state") for p in meta.get("playerStates", [])][1:]
    return {
        "seed": int(m.group(1)) if m else None,
        "ais": [a for a in ais if a],
        "states": states,
        "game_s": meta.get("timeElapsed", 0) / 1000,
        "dir": d.name,
    }


def kill_game(p):
    """Tue une partie et sa descendance. Le wrapper snap rend killpg
    parfois EPERM ; on double avec un kill direct puis pkill ciblé."""
    for attempt in (lambda: os.killpg(p.pid, signal.SIGKILL), p.kill):
        try:
            attempt()
        except (ProcessLookupError, PermissionError):
            pass
    subprocess.run(["pkill", "-9", "-P", str(p.pid)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_wave(matches, timeout=240, stagger=3.0):
    """Timeout PAR PARTIE : certaines parties ne se terminent jamais
    (seed 1 forkbot/petra : 3,4 M de tours constatés) — le timeout mural
    est notre cap de tours de facto."""
    procs = []
    for seed, ai1, ai2 in matches:
        p = subprocess.Popen(cmd_for(seed, ai1, ai2),
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             start_new_session=True)
        procs.append((p, time.monotonic()))
        time.sleep(stagger)
    killed = 0
    pending = list(procs)
    while pending:
        still = []
        now = time.monotonic()
        for p, ts in pending:
            if p.poll() is not None:
                continue
            if now - ts > timeout:
                kill_game(p)
                killed += 1
            else:
                still.append((p, ts))
        pending = still
        time.sleep(0.5)
    return killed


def main(n_seeds=12, parallel=12):
    OUT.mkdir(parents=True, exist_ok=True)
    matches = []
    for seed in range(1, n_seeds + 1):
        matches.append((seed, "forkbot", "petra"))
        matches.append((seed, "petra", "forkbot"))

    before = {p.name for p in REPLAYS.iterdir() if p.is_dir()}
    t0 = time.monotonic()
    for i in range(0, len(matches), parallel):
        wave = matches[i:i + parallel]
        print(f"vague {i // parallel + 1} : {len(wave)} parties…", flush=True)
        timeouts = run_wave(wave)
        if timeouts:
            print(f"  {timeouts} partie(s) tuée(s) au timeout", flush=True)
    wall = time.monotonic() - t0

    time.sleep(2)
    results = []
    for d in sorted(REPLAYS.iterdir()):
        if d.is_dir() and d.name not in before:
            r = parse_replay(d)
            if r and len(r["ais"]) == 2 and len(r["states"]) == 2:
                results.append(r)

    wins = {"forkbot": 0, "petra": 0}
    for r in results:
        for ai, state in zip(r["ais"], r["states"]):
            if state == "won":
                wins[ai] += 1
        print(f"  seed {r['seed']:2d} {r['ais'][0]:>7} vs {r['ais'][1]:<7} "
              f"-> {'/'.join(r['states'])} ({r['game_s'] / 60:.0f} min de jeu)")

    total = wins["forkbot"] + wins["petra"]
    print(f"\n{len(results)} replays exploités | mur total {wall:.0f}s")
    if total:
        print(f"forkbot {wins['forkbot']}/{total} "
              f"({100 * wins['forkbot'] / total:.0f}%) — "
              f"attendu ~50% (accepté : 7-17 sur 24)")
    (OUT / "results.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main(n_seeds=int(sys.argv[1]) if len(sys.argv) > 1 else 12)
