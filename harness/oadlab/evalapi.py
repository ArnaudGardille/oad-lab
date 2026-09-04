"""API programmatique d'évaluation — utilisée par le CLI et par la
boucle d'évolution (evolution/evaluator.py).

Contrairement au rapport CLI qui agrège tout l'historique d'un bot,
`evaluate_bot` rend les métriques du batch qu'il vient de jouer, et
enregistre les matchs en base sous un tag unique par version évaluée.
"""

import hashlib
import json

from . import config, db, descriptors, game, stats


def protocol_id(pool, pairs):
    """Empreinte du protocole d'évaluation (SPEC.md P3) : deux scores
    ne se comparent que si leurs protocoles sont identiques. Couvre
    tout ce qui change la distribution du résultat : pool
    d'adversaires, seeds, conditions de partie, budget de parties."""
    blob = json.dumps({
        "engine": config.GAME_VERSION,
        "pool": sorted(pool), "seeds": config.EVAL_SEEDS[:pairs],
        "map": config.MAP, "size": config.MAP_SIZE,
        "biome": config.BIOME, "civ": config.CIV,
        "cand_diff": config.CANDIDATE_DIFF,
        "timeout": config.GAME_TIMEOUT,
    }, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def opponents():
    """Le pool d'évaluation : les ancres Petra, plus le hall of fame
    s'il a été matérialisé (runs/hof.json). Les scores obtenus avec et
    sans hall of fame ne sont pas comparables entre eux."""
    pool = list(config.ANCHORS)
    if config.HOF_MANIFEST.exists():
        try:
            hof = json.loads(config.HOF_MANIFEST.read_text())
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"{config.HOF_MANIFEST} illisible ({e}) — "
                "relancer scripts/make_hof.py") from e
        for entry in hof:
            bot_dir = config.REPO / "bots/oadlab/simulation/ai" / entry["bot"]
            if not bot_dir.is_dir():
                # Un manifeste qui référence un bot absent produirait
                # des parties toutes perdues sans diagnostic : mieux
                # vaut échouer tout de suite.
                raise RuntimeError(
                    f"hof.json référence {entry['bot']} mais {bot_dir} "
                    "n'existe pas — relancer scripts/make_hof.py")
            pool.append((entry["bot"], config.CANDIDATE_DIFF))
    return pool


def eval_specs(bot, pairs, pool=None):
    """Les specs d'une évaluation standard : chaque adversaire × chaque
    seed d'éval × les deux positions. L'aiseed encode (seed, difficulté,
    position) ; les adversaires de même difficulté restent distinguables
    par les noms d'IA dans la clé d'attribution."""
    tagged = []
    for opponent, diff in pool or opponents():
        for seed in config.EVAL_SEEDS[:pairs]:
            base = seed * 1000 + diff * 10
            tagged.append((game.GameSpec(bot, opponent, config.CANDIDATE_DIFF,
                                         diff, seed, base + 1),
                           1, opponent, diff))
            tagged.append((game.GameSpec(opponent, bot, diff,
                                         config.CANDIDATE_DIFF, seed,
                                         base + 2), 2, opponent, diff))
    return tagged


def evaluate_bot(bot, pairs=4, tag=None, log=print):
    """Joue une évaluation complète et rend les métriques de CE batch.

    Les parties sans résultat (timeout, crash, replay inexploitable)
    comptent comme des DÉFAITES du candidat : un bot qui fige la partie
    ou fait planter le moteur ne doit jamais être récompensé.
    """
    tag = tag or bot
    pool = opponents()
    proto = protocol_id(pool, pairs)
    tagged = eval_specs(bot, pairs, pool)
    results = game.run_batch([spec for spec, *_ in tagged], log=log)
    by_key = {res["spec"].key(): res for res in results}

    con = db.connect()
    rows = []
    for spec, cand_pos, opponent, diff in tagged:
        res = by_key[spec.key()]
        r = res["replay"]
        cand_won = None
        game_s = turns = replay_dir = desc = None
        if r:
            turns, replay_dir = r["turns"], r["dir"]
            desc = descriptors.from_metadata(r["path"] / "metadata.json",
                                             cand_pos)
            if r["states"] and len(r["states"]) == 2:
                cand_won = int(r["states"][cand_pos - 1] == "won")
                game_s = r["game_s"]
        db.insert_match(con, candidate=tag, opponent=opponent, opp_diff=diff,
                        cand_pos=cand_pos, spec=spec, cand_won=cand_won,
                        timed_out=res["timed_out"], game_s=game_s,
                        turns=turns, wall_s=res["wall_s"],
                        replay_dir=replay_dir, descriptors=desc,
                        protocol=proto)
        rows.append({"opponent": opponent, "opp_diff": diff,
                     "cand_won": cand_won, "timed_out": res["timed_out"],
                     "game_s": game_s, "turns": turns, "desc": desc})
    con.close()

    metrics = {"games": len(rows),
               "no_result": sum(1 for r in rows if r["cand_won"] is None)}
    winrates = {}
    for opponent, diff in pool:
        sub = [r for r in rows
               if r["opponent"] == opponent and r["opp_diff"] == diff]
        # cand_won None -> 0 (défaite) : conservateur et non gameable
        winrates[(opponent, diff)] = \
            sum(r["cand_won"] or 0 for r in sub) / len(sub)
    metrics["wr_easy"] = winrates.get(("petra", 2), 0.0)
    metrics["wr_medium"] = winrates.get(("petra", 3), 0.0)
    metrics["wr_hard"] = winrates.get(("petra", 4), 0.0)
    hof_wr = [wr for (opp, _), wr in winrates.items() if opp != "petra"]
    if hof_wr:
        metrics["wr_hof"] = sum(hof_wr) / len(hof_wr)
    metrics["combined_score"] = sum(winrates.values()) / len(winrates)

    # Descripteurs comportementaux moyens du batch — c'est là-dessus
    # que MAP-Elites (feature_dimensions) place le programme. Sans
    # aucune partie exploitable, 0.0 partout : le programme est de
    # toute façon éliminé par son score.
    for key in db.DESCRIPTOR_COLS:
        vals = [r["desc"][key] for r in rows if r["desc"]]
        metrics[key] = sum(vals) / len(vals) if vals else 0.0

    sk = stats.openskill_rating(rows)
    if sk:
        metrics["ordinal"] = sk["ordinal"]

    decisive = [r for r in rows if r["cand_won"] is not None and r["game_s"]]
    if decisive:
        metrics["mean_game_min"] = sum(r["game_s"] for r in decisive) / \
            len(decisive) / 60
    return metrics
