#!/usr/bin/env python3
"""Phase 1 : mesure du débit d'évaluation en parallèle.

Lance N parties Petra vs Petra headless en parallèle, une par cœur
physique visé. Les instances partagent le dossier de données snap
(--writableRoot fige le jeu au démarrage, testé le 2026-09-02), donc les
démarrages sont décalés de quelques secondes pour éviter les collisions
de nommage des dossiers de replay, et chaque replay est réapparié à sa
partie via la seed inscrite dans commands.txt.

Chaque partie est lancée dans sa propre session (start_new_session) et
tuée par groupe de processus au timeout : le wrapper snap laisse sinon
un pyrogenesis orphelin derrière lui.
"""

import json
import os
import re
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPLAYS = Path.home() / "snap/0ad/current/.local/share/0ad/replays/0.28.0"
OUT = Path(__file__).resolve().parent.parent / "runs/phase1"
TURN_MS = 200

CMD = [
    "0ad",
    "--autostart=random/mainland",
    "--autostart-size=128",
    "--autostart-ai=1:petra",
    "--autostart-ai=2:petra",
    "--autostart-nonvisual",
]


def parse_replay(d):
    commands = d / "commands.txt"
    metadata = d / "metadata.json"
    if not commands.exists():
        return None
    header = commands.read_text(errors="replace")
    m = re.search(r'"Seed":\s*(\d+)', header)
    seed = int(m.group(1)) if m else None
    turns = header.count("\nturn ") + header.startswith("turn")
    result = {"seed": seed, "turns": turns, "dir": d.name}
    if metadata.exists():
        meta = json.loads(metadata.read_text())
        result["game_s"] = meta.get("timeElapsed", 0) / 1000
        states = [p.get("state") for p in meta.get("playerStates", [])]
        result["states"] = states[1:]
    return result


def main(n=12, stagger=3.0, timeout=600):
    OUT.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in REPLAYS.iterdir() if p.is_dir()}
    procs = {}
    for seed in range(1, n + 1):
        cmd = CMD + [f"--autostart-seed={seed}"]
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             start_new_session=True)
        procs[seed] = (p, time.monotonic())
        print(f"seed {seed:2d} lancée (pid {p.pid})", flush=True)
        time.sleep(stagger)

    walls, pending = {}, dict(procs)
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        for seed, (p, ts) in list(pending.items()):
            if p.poll() is not None:
                walls[seed] = time.monotonic() - ts
                print(f"seed {seed:2d} finie en {walls[seed]:.0f}s", flush=True)
                del pending[seed]
        time.sleep(0.5)
    for seed, (p, ts) in pending.items():
        print(f"seed {seed:2d} TIMEOUT, kill du groupe", flush=True)
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        walls[seed] = None

    time.sleep(2)
    replays = []
    for d in sorted(REPLAYS.iterdir()):
        if d.is_dir() and d.name not in before:
            r = parse_replay(d)
            if r:
                replays.append(r)

    rows = []
    for r in replays:
        wall = walls.get(r["seed"])
        game_s = r.get("game_s", 0)
        rows.append({**r, "wall_s": round(wall, 1) if wall else None,
                     "speedup": round(game_s / wall, 1) if wall and game_s else None})

    print(f"\n{'seed':>4} {'mur(s)':>7} {'jeu(min)':>8} {'tours':>7} "
          f"{'accél.':>7}  issue")
    for r in sorted(rows, key=lambda r: r.get("seed") or 0):
        states = "/".join(r.get("states", [])) or "?"
        game_min = r.get("game_s", 0) / 60
        print(f"{r['seed']:>4} {r['wall_s'] or 'T/O':>7} {game_min:>8.1f} "
              f"{r['turns']:>7} {r['speedup'] or '-':>7}  {states}")

    done = [r for r in rows if r["wall_s"]]
    if done:
        ws = [r["wall_s"] for r in done]
        print(f"\n{len(done)}/{n} finies | mur médian {statistics.median(ws):.0f}s"
              f" | min {min(ws):.0f}s | max {max(ws):.0f}s"
              f" | débit ~{3600 / statistics.median(ws) * n:.0f} parties/h"
              f" à parallélisme {n}")

    (OUT / "results.json").write_text(json.dumps(rows, indent=2))
    print(f"\nRésultats -> {OUT / 'results.json'}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    main(n=n)
