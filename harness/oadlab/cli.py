"""CLI du harnais : eval, report, selftest, snapshot, watch, play."""

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config, db, evalapi, game, replay, stats


def cmd_eval(args):
    n = len(config.ANCHORS) * args.pairs * 2
    print(f"éval de {args.bot} : {n} parties "
          f"({len(config.ANCHORS)} ancres × {args.pairs} seeds × 2 positions)")
    t0 = time.monotonic()
    metrics = evalapi.evaluate_bot(args.bot, pairs=args.pairs)
    print(f"batch terminé en {time.monotonic() - t0:.0f}s | "
          f"combined_score {metrics['combined_score']:.3f}")
    report(db.connect(), args.bot)


def report(con, bot):
    rows = db.matches_for(con, bot)
    if not rows:
        print(f"aucun match en base pour {bot}")
        return
    print(f"\n=== {bot} — {len(rows)} matchs en base ===")
    for anchor, diff in config.ANCHORS:
        sub = [r for r in rows
               if r["opponent"] == anchor and r["opp_diff"] == diff]
        dec = [r for r in sub if r["cand_won"] is not None]
        nores = len(sub) - len(dec)
        k = sum(r["cand_won"] or 0 for r in sub)   # no-result = défaite, cf. combined_score
        lo, hi = stats.wilson(k, len(sub))
        extra = f" | {nores} sans résultat" if nores else ""
        label = {2: "facile", 3: "moyen", 4: "dur"}.get(diff, str(diff))
        if sub:
            print(f"  vs petra {label:<7}: {k:>2}/{len(sub)} "
                  f"({100 * k / len(sub):3.0f}%)  IC95 "
                  f"[{100 * lo:.0f}–{100 * hi:.0f}%]{extra}")
        else:
            print(f"  vs petra {label:<7}: aucune partie")
    k = sum(r["cand_won"] or 0 for r in rows)   # no-result = défaite, cf. combined_score
    if rows:
        lo, hi = stats.wilson(k, len(rows))
        print(f"  global          : {k:>2}/{len(rows)} "
              f"({100 * k / len(rows):3.0f}%)  IC95 [{100 * lo:.0f}–{100 * hi:.0f}%]")
    sk = stats.openskill_rating(rows)
    if sk:
        print(f"  openskill       : ordinal {sk['ordinal']:.1f} "
              f"(mu {sk['mu']:.1f}, sigma {sk['sigma']:.2f}, "
              f"{sk['games']} matchs)")


def cmd_report(args):
    report(db.connect(), args.bot)


def cmd_selftest(args):
    """Sanité du pipeline + sonde de reproductibilité (informative).

    Constaté le 2026-09-02 : même avec seed, aiseed, biome et civs
    figés, deux runs divergent (l'IA calcule dans un thread asynchrone,
    ses commandes dépendent du timing CPU). L'évaluation est donc
    statistique par nature ; la sonde échoue seulement si les replays
    sont inexploitables, pas si les parties diffèrent."""
    spec = game.GameSpec("petra", "petra", 3, 3, seed=101, aiseed=7)
    print("sonde : même partie jouée deux fois…")
    outcomes = []
    for _ in range(2):
        [res] = game.run_batch([spec], parallel=1)
        r = res["replay"]
        if not r or not r["states"]:
            print("  ÉCHEC : pas de replay exploitable")
            return 1
        outcomes.append((r["turns"], tuple(r["states"])))
        print(f"  tours={r['turns']} états={r['states']}")
    if outcomes[0] == outcomes[1]:
        print("  parties identiques sur cette sonde")
    else:
        print("  parties différentes (attendu : IA asynchrone) — "
              "l'évaluation reste statistique")
    print("  OK : pipeline exploitable")
    return 0


def _make_bot():
    """`make_bot` vit dans scripts/ (pas un paquet) : on l'importe par
    chemin plutôt que de dupliquer ses règles de réécriture des imports
    JS — un bot dont les imports pointent ailleurs charge le code d'un
    autre bot sans le moindre message d'erreur."""
    sys.path.insert(0, str(config.REPO / "scripts"))
    from make_candidate import make_bot
    return make_bot


def _best_program(run=None):
    """Le meilleur programme écrit le plus récemment : best/ pour un run
    terminé, dernier checkpoint pour un run en cours. Le tri est par
    date d'écriture et non par nom de run — sinon `smoke` gagne contre
    toutes les nuits datées."""
    root = config.RUNS / "evolution"
    if not root.is_dir():
        return None
    dirs = [root / run] if run else [d for d in root.iterdir() if d.is_dir()]
    files = [d / "best/best_program.js" for d in dirs] + \
            [c / "best_program.js" for d in dirs
             for c in d.glob("checkpoints/checkpoint_*")]
    files = [f for f in files if f.exists()]
    return max(files, key=lambda p: p.stat().st_mtime, default=None)


def cmd_snapshot(args):
    """Fige un programme évolué dans un bot stable et jouable.

    `candidate` ne convient ni au visionnage ni au duel : la boucle
    écrase son config.js à chaque itération, on regarderait un autre
    bot que celui qu'on croit. On matérialise donc une copie nommée,
    comme make_hof.py le fait pour le hall of fame."""
    src = Path(args.source) if args.source else _best_program(args.run)
    if src is None:
        print("aucun best_program.js trouvé sous runs/evolution/ — "
              "préciser --from <config.js>")
        return 1
    src = src.resolve()
    if not src.exists():
        print(f"source introuvable : {src}")
        return 1
    dest = _make_bot()(args.bot, f"Snapshot oad-lab de {src.name} "
                                 f"({args.bot}) — bot figé, jouable.")
    # Le config.js évolué importe les modules du bot pour lequel il a
    # été produit (candidate/) : sans réécriture, le snapshot suivrait
    # les modifications de ce bot-là.
    code = re.sub(r"simulation/ai/(candidate|forkbot)/",
                  f"simulation/ai/{args.bot}/", src.read_text())
    (dest / "config.js").write_text(code)
    rel = src.relative_to(config.REPO) if src.is_relative_to(config.REPO) \
        else src
    print(f"{args.bot} figé depuis {rel}\n"
          f"  → {dest.relative_to(config.REPO)}\n"
          f"  jouable : oadlab watch {args.bot} | oadlab play {args.bot}")
    return 0


def _launch(spec, unit=None):
    """Lance une partie visible. Détachée, elle passe par systemd :
    lancée depuis un shell qui se termine (agent, script), la partie
    meurt avec lui."""
    print(" ".join(spec.cmd()))
    if not unit:
        return subprocess.call(spec.cmd())
    argv = list(spec.cmd())
    argv[0] = shutil.which(argv[0]) or argv[0]   # systemd exige un chemin absolu
    rc = subprocess.call(["systemd-run", "--user", f"--unit={unit}",
                          "--collect", *argv])
    if rc == 0:
        print(f"partie détachée — arrêt : systemctl --user stop {unit}")
    return rc


def _aiseed(args, diff):
    """Par défaut, l'aiseed d'une spec d'évaluation (evalapi.eval_specs,
    position 1) : la partie regardée est alors celle du protocole."""
    if args.aiseed is not None:
        return args.aiseed
    if args.seed < 0:            # carte aléatoire : IA aléatoire aussi
        return -1
    return args.seed * 1000 + diff * 10 + 1


def cmd_watch(args):
    spec = game.GameSpec(args.bot, args.vs, args.diff, args.vs_diff,
                         seed=args.seed, aiseed=_aiseed(args, args.vs_diff),
                         player=-1, speed=args.speed)
    return _launch(spec, args.unit if args.detach else None)


def cmd_play(args):
    """Toi joueur 1, le bot joueur 2, aux conditions du protocole."""
    spec = game.GameSpec(None, args.bot, config.CANDIDATE_DIFF, args.diff,
                         seed=args.seed, aiseed=_aiseed(args, args.diff),
                         player=1)
    return _launch(spec, args.unit if args.detach else None)


def _visual_args(p, detach_unit):
    p.add_argument("bot")
    p.add_argument("--diff", type=int, default=config.CANDIDATE_DIFF,
                   help="difficulté du bot (défaut : celle de l'éval)")
    p.add_argument("--seed", type=int, default=101,
                   help="seed de carte (-1 : aléatoire)")
    p.add_argument("--aiseed", type=int, default=None,
                   help="défaut : l'aiseed de la spec d'éval correspondante")
    p.add_argument("--detach", action="store_true",
                   help="lance la partie en tâche de fond (systemd)")
    p.add_argument("--unit", default=detach_unit,
                   help="nom de l'unité systemd avec --detach")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="oadlab")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("eval", help="évalue un bot contre les ancres")
    e.add_argument("bot")
    e.add_argument("--pairs", type=int, default=4,
                   help="seeds par ancre (2 parties mirrorées chacune)")
    e.set_defaults(func=cmd_eval)
    r = sub.add_parser("report", help="réimprime le rapport depuis la base")
    r.add_argument("bot")
    r.set_defaults(func=cmd_report)
    s = sub.add_parser("selftest", help="vérifie le déterminisme du pipeline")
    s.set_defaults(func=cmd_selftest)

    sn = sub.add_parser("snapshot",
                        help="fige un programme évolué dans un bot stable")
    sn.add_argument("bot", nargs="?", default="watchbot")
    sn.add_argument("--from", dest="source",
                    help="config.js source (défaut : meilleur programme)")
    sn.add_argument("--run", help="run d'où tirer le meilleur programme "
                                  "(défaut : le plus récent)")
    sn.set_defaults(func=cmd_snapshot)

    w = sub.add_parser("watch",
                       help="regarde une partie du bot en observateur")
    _visual_args(w, "oad-watch")
    w.add_argument("--vs", default="petra", help="adversaire (défaut petra)")
    w.add_argument("--vs-diff", type=int, default=config.CANDIDATE_DIFF,
                   help="difficulté de l'adversaire")
    w.add_argument("--speed", type=int, default=5,
                   help="vitesse de simulation (max 20 en observateur)")
    w.set_defaults(func=cmd_watch)

    p = sub.add_parser("play", help="joue contre le bot (toi joueur 1)")
    _visual_args(p, "oad-play")
    p.set_defaults(func=cmd_play)

    args = ap.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
