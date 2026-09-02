"""Lancement et supervision des parties headless."""

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass

from . import config, replay


@dataclass
class GameSpec:
    ai1: str
    ai2: str
    diff1: int
    diff2: int
    seed: int
    aiseed: int
    map: str = config.MAP
    size: int = config.MAP_SIZE
    biome: str = config.BIOME
    civ: str = config.CIV

    def cmd(self):
        return [
            config.GAME_CMD,
            *[f"--mod={m}" for m in config.MODS],
            f"--autostart={self.map}",
            f"--autostart-size={self.size}",
            f"--autostart-biome={self.biome}",
            f"--autostart-seed={self.seed}",
            f"--autostart-aiseed={self.aiseed}",
            f"--autostart-civ=1:{self.civ}",
            f"--autostart-civ=2:{self.civ}",
            f"--autostart-ai=1:{self.ai1}",
            f"--autostart-ai=2:{self.ai2}",
            f"--autostart-aidiff=1:{self.diff1}",
            f"--autostart-aidiff=2:{self.diff2}",
            "--autostart-nonvisual",
        ]

    def key(self):
        return (self.seed, self.aiseed,
                (self.ai1, self.ai2), (self.diff1, self.diff2))


def _descendants(pid):
    out = subprocess.run(["pgrep", "-P", str(pid)],
                         capture_output=True, text=True)
    pids = []
    for child in (int(x) for x in out.stdout.split()):
        pids.append(child)
        pids.extend(_descendants(child))
    return pids


def kill_game(p):
    """Le wrapper snap rend killpg parfois EPERM, et pyrogenesis est un
    petit-fils du wrapper : on relève toute la descendance AVANT de
    tuer (après, elle est reparentée sur init et devient introuvable),
    puis on tue groupe, parent et descendants un par un."""
    victims = _descendants(p.pid)
    for attempt in (lambda: os.killpg(p.pid, signal.SIGKILL), p.kill):
        try:
            attempt()
        except (ProcessLookupError, PermissionError):
            pass
    for pid in victims:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def run_batch(specs, parallel=None, timeout=None, stagger=None, log=print):
    """Joue toutes les specs (pool borné, timeout par partie), moissonne
    les replays vers runs/matches/, et rend une liste de résultats
    {spec, wall_s, timed_out, replay}."""
    parallel = parallel or config.PARALLEL
    timeout = timeout or config.GAME_TIMEOUT
    stagger = stagger or config.STAGGER
    keys = [s.key() for s in specs]
    assert len(set(keys)) == len(keys), \
        "specs à key() identique dans un même batch : replays inattribuables"

    config.MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in config.SNAP_REPLAYS.iterdir() if p.is_dir()} \
        if config.SNAP_REPLAYS.exists() else set()

    queue = list(specs)
    running, done = [], []
    last_launch = 0.0
    while queue or running:
        now = time.monotonic()
        if queue and len(running) < parallel and now - last_launch >= stagger:
            spec = queue.pop(0)
            p = subprocess.Popen(spec.cmd(), stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            running.append((spec, p, now))
            last_launch = now
        for item in running[:]:
            spec, p, ts = item
            if p.poll() is not None:
                done.append({"spec": spec, "wall_s": now - ts,
                             "timed_out": False})
                running.remove(item)
            elif now - ts > timeout:
                kill_game(p)
                log(f"  timeout: {spec.ai1}/{spec.ai2} seed {spec.seed}")
                done.append({"spec": spec, "wall_s": timeout,
                             "timed_out": True})
                running.remove(item)
        time.sleep(0.2)

    time.sleep(2)
    parsed = {}
    new_dirs = sorted(config.SNAP_REPLAYS.iterdir()) \
        if config.SNAP_REPLAYS.exists() else []
    for d in new_dirs:
        if d.is_dir() and d.name not in before:
            r = replay.parse(d)
            if r:
                parsed[replay.key(r)] = r

    for res in done:
        r = parsed.get(res["spec"].key())
        res["replay"] = r
        if r:
            dest = config.MATCHES_DIR / r["dir"]
            if not dest.exists():
                shutil.move(str(r["path"]), str(dest))
            r["path"] = dest
    return done
