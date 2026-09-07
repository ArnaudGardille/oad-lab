"""API programmatique d'évaluation — utilisée par le CLI et par la
boucle d'évolution (evolution/evaluator.py).

Contrairement au rapport CLI qui agrège tout l'historique d'un bot,
`evaluate_bot` rend les métriques du batch qu'il vient de jouer, et
enregistre les matchs en base sous un tag unique par version évaluée.
"""

import hashlib
import json

from . import config, db, descriptors, game, stats


def protocol_id(pool, seeds):
    """Empreinte du protocole d'évaluation (SPEC.md P3) : deux scores
    ne se comparent que si leurs protocoles sont identiques. Couvre
    tout ce qui change la distribution du résultat : pool
    d'adversaires, seeds, conditions de partie, budget de parties.

    `behavior` en fait partie depuis le 2026-09-04 : épingler la
    personnalité change la distribution des résultats, donc les
    scores d'avant et d'après ne sont pas comparables."""
    blob = json.dumps({
        "engine": config.GAME_VERSION,
        "pool": sorted(pool), "seeds": list(seeds),
        "map": config.MAP, "size": config.MAP_SIZE,
        "biome": config.BIOME, "civ": config.CIV,
        "cand_diff": config.CANDIDATE_DIFF,
        "behavior": config.AI_BEHAVIOR,
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
            # Même exigence pour l'épinglage de la personnalité : un
            # bot du pool matérialisé avant AI_BEHAVIOR joue une
            # persona tirée au hasard à chaque partie, et le
            # protocol_id enregistré affirmerait le contraire (P3).
            pin = f'behavior = "{config.AI_BEHAVIOR}"'
            if pin not in (bot_dir / "_petrabot.js").read_text(
                    errors="replace"):
                raise RuntimeError(
                    f"{entry['bot']} date d'avant l'épinglage de la "
                    f"personnalité ({pin} absent de son _petrabot.js) : "
                    "il jouerait une persona aléatoire sous un protocole "
                    "qui prétend le contraire — relancer "
                    "scripts/make_hof.py pour le régénérer")
            pool.append((entry["bot"], config.CANDIDATE_DIFF))
    return pool


def scores_under(protocol, con=None):
    """Les combined_score de SÉLECTION déjà mesurés sous ce protocole,
    un par programme, recalculés depuis les matchs bruts (P2).

    Sert à situer un candidat dans la population qu'il vient de
    rejoindre — comparaison qui n'a de sens qu'à protocole égal (P3),
    d'où le filtre. Les tags de cascade sont exclus : `-s1` n'est
    qu'une sonde à une seed, `-s3` est une confirmation sur d'autres
    seeds, ni l'un ni l'autre n'est un score de sélection."""
    own = con or db.connect()
    try:
        rows = own.execute(
            "SELECT candidate, opponent, opp_diff, cand_won FROM matches"
            " WHERE protocol = ? AND candidate LIKE 'cand-%'"
            " AND candidate NOT LIKE '%-s1' AND candidate NOT LIKE '%-s3'",
            (protocol,)).fetchall()
    finally:
        if con is None:
            own.close()
    per = {}
    for r in rows:
        per.setdefault(r["candidate"], {}) \
           .setdefault((r["opponent"], r["opp_diff"]), []) \
           .append(r["cand_won"] or 0)   # sans résultat = défaite
    return {cand: sum(sum(v) / len(v) for v in by_opp.values()) / len(by_opp)
            for cand, by_opp in per.items()}


def eval_specs(bot, seeds, pool=None):
    """Les specs d'une évaluation standard : chaque adversaire × chaque
    seed demandé × les deux positions. L'aiseed encode (seed, difficulté,
    position) ; les adversaires de même difficulté restent distinguables
    par les noms d'IA dans la clé d'attribution."""
    tagged = []
    for opponent, diff in pool or opponents():
        for seed in seeds:
            base = seed * 1000 + diff * 10
            tagged.append((game.GameSpec(bot, opponent, config.CANDIDATE_DIFF,
                                         diff, seed, base + 1),
                           1, opponent, diff))
            tagged.append((game.GameSpec(opponent, bot, diff,
                                         config.CANDIDATE_DIFF, seed,
                                         base + 2), 2, opponent, diff))
    return tagged


def evaluate_bot(bot, pairs=4, tag=None, log=print, seeds=None):
    """Joue une évaluation complète et rend les métriques de CE batch.

    Les parties sans résultat (timeout, crash, replay inexploitable)
    comptent comme des DÉFAITES du candidat : un bot qui fige la partie
    ou fait planter le moteur ne doit jamais être récompensé.

    `seeds` prime sur `pairs` : le stage 3 de confirmation rejoue les
    élites sur CONFIRM_SEEDS, disjoint d'EVAL_SEEDS, pour que le score
    qui promeut un programme ne soit pas le tirage qui l'a fait gagner.
    """
    tag = tag or bot
    seeds = list(seeds) if seeds is not None else config.EVAL_SEEDS[:pairs]
    pool = opponents()
    proto = protocol_id(pool, seeds)
    tagged = eval_specs(bot, seeds, pool)
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
