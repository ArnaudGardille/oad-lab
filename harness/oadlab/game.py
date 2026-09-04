"""Lancement et supervision des parties headless."""

import fcntl
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

from . import config, replay

_BATCH_LOCK = config.RUNS / ".game_batch.lock"


@dataclass
class GameSpec:
    """Un match, quelle que soit la façon de le regarder.

    `player` décide de la sortie : None = headless (le seul mode que
    l'évaluation utilise), -1 = observateur, 1 = humain au clavier.
    Un slot dont l'IA vaut None reste humain — c'est ainsi qu'on joue
    contre un bot sans redéfinir ailleurs les conditions de partie."""

    ai1: str | None
    ai2: str | None
    diff1: int
    diff2: int
    seed: int
    aiseed: int
    map: str = config.MAP
    size: int = config.MAP_SIZE
    biome: str = config.BIOME
    civ: str = config.CIV
    player: int | None = None
    speed: int | None = None   # cap moteur : 2 en jeu, 20 en observateur

    def cmd(self):
        args = [
            config.GAME_CMD,
            *[f"--mod={m}" for m in config.MODS],
            f"--autostart={self.map}",
            f"--autostart-size={self.size}",
            f"--autostart-biome={self.biome}",
            f"--autostart-seed={self.seed}",
            f"--autostart-aiseed={self.aiseed}",
            f"--autostart-civ=1:{self.civ}",
            f"--autostart-civ=2:{self.civ}",
        ]
        for slot, ai, diff in ((1, self.ai1, self.diff1),
                               (2, self.ai2, self.diff2)):
            if ai:
                args += [f"--autostart-ai={slot}:{ai}",
                         f"--autostart-aidiff={slot}:{diff}"]
        if self.player is None:
            args.append("--autostart-nonvisual")
        else:
            args.append(f"--autostart-player={self.player}")
            if self.speed:
                args.append(f"--autostart-speed={self.speed}")
        return args

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


def _unconfined_kill(args):
    """Signal délégué à systemd --user (unconfined) : quand le harnais
    tourne sous un label AppArmor de session (p. ex. claude-desktop),
    TOUS ses kill() vers les processus snap sont EPERM — chaque partie
    en timeout survivait alors indéfiniment, 3-10 Go de RSS chacune,
    jusqu'à saturer les 128 Go (nuits des 2026-09-03/04).

    Un échec ici ne doit JAMAIS remonter : planter la nuit entière est
    pire que fuir une partie — le reaper (scripts/reap_hung_games.sh)
    finira le travail. On logge sur stderr (-> night.log)."""
    try:
        r = subprocess.run(["systemd-run", "--user", "--quiet",
                            "--collect", "--wait", "/bin/kill",
                            "-KILL", "--"] + [str(a) for a in args],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
        if r.returncode != 0:
            print(f"  unconfined kill rc={r.returncode} pour {args} —"
                  " parties laissées au reaper", file=sys.stderr)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  unconfined kill impossible ({e}) pour {args} —"
              " parties laissées au reaper", file=sys.stderr)


def kill_game(p):
    """Le wrapper snap rend killpg parfois EPERM, et pyrogenesis est un
    petit-fils du wrapper : on relève toute la descendance AVANT de
    tuer (après, elle est reparentée sur init et devient introuvable),
    puis on tue groupe, parent et descendants un par un. Ce qui reste
    EPERM part en dernier recours via systemd (voir _unconfined_kill)."""
    victims = _descendants(p.pid)
    denied = []
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        denied.append(f"-{p.pid}")
    try:
        p.kill()
    except (ProcessLookupError, PermissionError):
        pass
    for pid in victims:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            denied.append(pid)
    if denied:
        _unconfined_kill(denied)


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

    config.RUNS.mkdir(parents=True, exist_ok=True)
    lockf = open(_BATCH_LOCK, "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lockf.close()
        raise RuntimeError(
            "un autre batch de parties tourne déjà (verrou "
            f"{_BATCH_LOCK}) — l'attribution des replays serait fausse")
    try:
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
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()
