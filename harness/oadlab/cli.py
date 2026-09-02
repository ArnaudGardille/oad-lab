"""CLI du harnais : eval, report, selftest."""

import argparse
import sys
import time

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
        k = sum(r["cand_won"] for r in dec)
        lo, hi = stats.wilson(k, len(dec))
        nores = len(sub) - len(dec)
        extra = f" | {nores} sans résultat" if nores else ""
        label = {2: "facile", 3: "moyen", 4: "dur"}.get(diff, str(diff))
        if dec:
            print(f"  vs petra {label:<7}: {k:>2}/{len(dec)} "
                  f"({100 * k / len(dec):3.0f}%)  IC95 "
                  f"[{100 * lo:.0f}–{100 * hi:.0f}%]{extra}")
        else:
            print(f"  vs petra {label:<7}: aucune partie décisive{extra}")
    dec = [r for r in rows if r["cand_won"] is not None]
    k = sum(r["cand_won"] for r in dec)
    if dec:
        lo, hi = stats.wilson(k, len(dec))
        print(f"  global          : {k:>2}/{len(dec)} "
              f"({100 * k / len(dec):3.0f}%)  IC95 [{100 * lo:.0f}–{100 * hi:.0f}%]")
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
    args = ap.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
