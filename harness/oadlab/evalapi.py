"""API programmatique d'évaluation — utilisée par le CLI et par la
boucle d'évolution (evolution/evaluator.py).

Contrairement au rapport CLI qui agrège tout l'historique d'un bot,
`evaluate_bot` rend les métriques du batch qu'il vient de jouer, et
enregistre les matchs en base sous un tag unique par version évaluée.
"""

from . import config, db, game, stats


def eval_specs(bot, pairs):
    """Les specs d'une évaluation standard : chaque ancre × chaque seed
    d'éval × les deux positions. L'aiseed encode (seed, difficulté,
    position) pour garantir l'unicité des clés d'attribution."""
    tagged = []
    for anchor, diff in config.ANCHORS:
        for seed in config.EVAL_SEEDS[:pairs]:
            base = seed * 1000 + diff * 10
            tagged.append((game.GameSpec(bot, anchor, config.CANDIDATE_DIFF,
                                         diff, seed, base + 1), 1, anchor, diff))
            tagged.append((game.GameSpec(anchor, bot, diff,
                                         config.CANDIDATE_DIFF, seed, base + 2), 2, anchor, diff))
    return tagged


def evaluate_bot(bot, pairs=4, tag=None, log=print):
    """Joue une évaluation complète et rend les métriques de CE batch.

    Les parties sans résultat (timeout, crash, replay inexploitable)
    comptent comme des DÉFAITES du candidat : un bot qui fige la partie
    ou fait planter le moteur ne doit jamais être récompensé.
    """
    tag = tag or bot
    tagged = eval_specs(bot, pairs)
    results = game.run_batch([spec for spec, *_ in tagged], log=log)
    by_key = {res["spec"].key(): res for res in results}

    con = db.connect()
    rows = []
    for spec, cand_pos, anchor, diff in tagged:
        res = by_key[spec.key()]
        r = res["replay"]
        cand_won = None
        game_s = turns = replay_dir = None
        if r:
            turns, replay_dir = r["turns"], r["dir"]
            if r["states"] and len(r["states"]) == 2:
                cand_won = int(r["states"][cand_pos - 1] == "won")
                game_s = r["game_s"]
        db.insert_match(con, candidate=tag, opponent=anchor, opp_diff=diff,
                        cand_pos=cand_pos, spec=spec, cand_won=cand_won,
                        timed_out=res["timed_out"], game_s=game_s,
                        turns=turns, wall_s=res["wall_s"],
                        replay_dir=replay_dir)
        rows.append({"opponent": anchor, "opp_diff": diff,
                     "cand_won": cand_won, "timed_out": res["timed_out"],
                     "game_s": game_s, "turns": turns})
    con.close()

    metrics = {"games": len(rows),
               "no_result": sum(1 for r in rows if r["cand_won"] is None)}
    winrates = {}
    for anchor, diff in config.ANCHORS:
        sub = [r for r in rows
               if r["opponent"] == anchor and r["opp_diff"] == diff]
        # cand_won None -> 0 (défaite) : conservateur et non gameable
        winrates[(anchor, diff)] = \
            sum(r["cand_won"] or 0 for r in sub) / len(sub)
    metrics["wr_easy"] = winrates.get(("petra", 2), 0.0)
    metrics["wr_medium"] = winrates.get(("petra", 3), 0.0)
    metrics["wr_hard"] = winrates.get(("petra", 4), 0.0)
    metrics["combined_score"] = sum(winrates.values()) / len(winrates)

    sk = stats.openskill_rating(rows)
    if sk:
        metrics["ordinal"] = sk["ordinal"]

    decisive = [r for r in rows if r["cand_won"] is not None and r["game_s"]]
    if decisive:
        metrics["mean_game_min"] = sum(r["game_s"] for r in decisive) / \
            len(decisive) / 60
    return metrics
