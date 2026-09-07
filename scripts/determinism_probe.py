#!/usr/bin/env python3
"""Mesure la reproductibilité d'UNE partie, à charge machine variable.

Pourquoi : le 2026-09-04, en rejouant des programmes identiques sur des
seeds identiques, 71 à 79 % des couples (seed, aiseed, adversaire,
position) rendaient une issue DIFFÉRENTE. Les seeds figés (SPEC.md P3)
n'achetaient donc aucune réduction de variance, et l'évaluation ne
pouvait pas distinguer une stratégie d'un tirage chanceux. Ce script
tranche entre les deux causes possibles :

- si le moteur est non déterministe en soi, les issues varient même
  machine calme (bras SEQ, une partie à la fois) ;
- si c'est un artefact de charge — 0 A.D. budgète le calcul de l'IA par
  tour, donc sous 12 parties concurrentes l'IA « pense » moins —, SEQ
  est stable et seul le bras LOAD bascule.

Le signal fin n'est pas la victoire mais le NOMBRE DE TOURS : deux
exécutions vraiment déterministes rendent la même partie au tour près.
L'issue peut coïncider par hasard, pas la durée.

Les parties sont enregistrées en base (P1) sous les tags `probe-seq` /
`probe-load`, avec leur protocole — ce sont des faits comme les autres.

Usage : scripts/determinism_probe.py [répétitions]   (défaut : 8)
"""

import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))

from oadlab import config, db, evalapi, game  # noqa: E402

# La partie sondée : le candidat contre l'ancre de sa propre
# difficulté, position 1, premier seed d'éval — exactement une des
# parties que joue chaque évaluation.
PROBE_SEED = config.EVAL_SEEDS[0]
PROBE_OPP, PROBE_DIFF = "petra", config.CANDIDATE_DIFF


def probe_spec():
    return game.GameSpec("candidate", PROBE_OPP, config.CANDIDATE_DIFF,
                         PROBE_DIFF, PROBE_SEED,
                         PROBE_SEED * 1000 + PROBE_DIFF * 10 + 1)


def filler_specs(n, salt):
    """Parties de REMPLISSAGE : elles n'existent que pour occuper les
    autres cœurs. Seeds pris hors EVAL_SEEDS et hors CONFIRM_SEEDS pour
    ne polluer aucun protocole d'évaluation, et aiseeds tous distincts
    (run_batch refuse deux specs de même clé)."""
    out = []
    for i in range(n):
        seed = 900 + salt * 20 + i
        out.append(game.GameSpec("candidate", PROBE_OPP,
                                 config.CANDIDATE_DIFF, PROBE_DIFF,
                                 seed, seed * 1000 + i))
    return out


def record(con, tag, spec, res, proto):
    r = res["replay"]
    cand_won = turns = game_s = replay_dir = None
    if r:
        turns, replay_dir = r["turns"], r["dir"]
        if r["states"] and len(r["states"]) == 2:
            cand_won = int(r["states"][0] == "won")
            game_s = r["game_s"]
    db.insert_match(con, candidate=tag, opponent=PROBE_OPP,
                    opp_diff=PROBE_DIFF, cand_pos=1, spec=spec,
                    cand_won=cand_won, timed_out=res["timed_out"],
                    game_s=game_s, turns=turns, wall_s=res["wall_s"],
                    replay_dir=replay_dir, descriptors=None,
                    protocol=proto)
    return {"won": cand_won, "turns": turns, "game_s": game_s,
            "wall_s": res["wall_s"], "timed_out": res["timed_out"]}


def run_arm(con, tag, reps, parallel, proto, log):
    """Rejoue `reps` fois LA MÊME partie. Une répétition = un batch :
    run_batch interdit deux specs de clé identique dans un même lot,
    et c'est bien la même clé qu'on veut rejouer."""
    rows = []
    for i in range(reps):
        specs = [probe_spec()]
        if parallel > 1:
            specs += filler_specs(parallel - 1, i)
        results = game.run_batch(specs, parallel=parallel,
                                 log=lambda *a: None)
        probe_key = specs[0].key()
        res = next(r for r in results if r["spec"].key() == probe_key)
        row = record(con, tag, specs[0], res, proto)
        rows.append(row)
        log(f"  {tag} {i + 1}/{reps}: "
            f"issue={'V' if row['won'] == 1 else 'D' if row['won'] == 0 else '?'} "
            f"tours={row['turns']} durée={row['game_s'] and round(row['game_s'] / 60, 1)} min "
            f"mur={row['wall_s']:.0f} s")
    return rows


# Part minimale de parties exploitables pour qu'un bras ait valeur de
# preuve. En dessous, la sonde se tait plutôt que de conclure.
MIN_USABLE = 0.75


def summarize(name, rows):
    outcomes = Counter(r["won"] for r in rows)
    turns = [r["turns"] for r in rows if r["turns"]]
    n = len(rows)
    majority = max(outcomes.values())
    flip = 1 - majority / n
    print(f"\n{name} ({n} rejeux de la MÊME partie)")
    print(f"  issues        : {dict(outcomes)}  -> taux de bascule "
          f"{flip:.0%}")
    if turns:
        print(f"  tours         : {len(set(turns))} valeur(s) distincte(s) "
              f"sur {len(turns)} — min {min(turns)} max {max(turns)}"
              + (f" écart-type {statistics.pstdev(turns):.0f}"
                 if len(turns) > 1 else ""))
    print(f"  mur moyen     : {statistics.mean(r['wall_s'] for r in rows):.0f} s"
          f"  timeouts : {sum(r['timed_out'] for r in rows)}")
    return {"flip": flip, "distinct_turns": len(set(turns)) if turns else None,
            # Parties exploitables : celles qui ont produit un replay
            # lisible. Une issue None (timeout, moteur planté) compte
            # dans `outcomes` et gonfle donc `flip` — sans ce compte,
            # sept parties plantées sur huit passaient pour la preuve
            # d'un moteur non déterministe.
            "usable": len(turns), "n": n}


def main():
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    proto = evalapi.protocol_id([(PROBE_OPP, PROBE_DIFF)], [PROBE_SEED])
    print(f"Sonde de déterminisme — {reps} rejeux par bras, "
          f"protocole {proto}")
    print(f"partie : candidate(diff {config.CANDIDATE_DIFF}) vs "
          f"{PROBE_OPP}(diff {PROBE_DIFF}), seed {PROBE_SEED}, position 1\n")

    con = db.connect()
    try:
        print(f"bras SEQ — 1 partie à la fois (machine calme)")
        seq = run_arm(con, "probe-seq", reps, 1, proto, print)
        print(f"\nbras LOAD — {config.PARALLEL} parties concurrentes")
        load = run_arm(con, "probe-load", reps, config.PARALLEL, proto, print)
    finally:
        con.close()

    s = summarize("SEQ  (machine calme)", seq)
    L = summarize(f"LOAD ({config.PARALLEL} en parallèle)", load)

    print("\n--- verdict ---")
    # Un bras dont les parties plantent ne dit rien du déterminisme :
    # les issues None comptent comme une issue distincte et feraient
    # conclure « PAS déterministe » à partir d'une seule partie
    # réellement jouée. On exige une majorité de parties exploitables
    # dans les DEUX bras avant de conclure quoi que ce soit.
    usable = min(s["usable"] / s["n"], L["usable"] / L["n"])
    if usable < MIN_USABLE:
        print(f"DONNÉES INSUFFISANTES : seulement {usable:.0%} de parties "
              f"exploitables dans le bras le plus abîmé (minimum "
              f"{MIN_USABLE:.0%}). Rien ne peut être conclu sur le "
              "déterminisme — vérifier le harnais, puis relancer.")
        return 1
    if s["distinct_turns"] == 1 and s["flip"] == 0:
        if L["flip"] > 0 or (L["distinct_turns"] or 0) > 1:
            print("Le moteur est DÉTERMINISTE à charge nulle et cesse de "
                  "l'être sous charge : la variance observée est un "
                  "artefact du parallélisme (budget de calcul de l'IA par "
                  "tour). Réduire PARALLEL rend les seeds utiles.")
        else:
            print("DÉTERMINISTE dans les deux bras : la variance des "
                  "évaluations vient d'ailleurs (pool, descripteurs, "
                  "attribution des replays) — enquêter plus loin.")
    else:
        print("Le moteur n'est PAS déterministe même machine calme : les "
              "seeds figés n'achètent aucune réduction de variance. Il "
              "faut payer la précision en NOMBRE DE PARTIES ; viser un "
              "protocole reproductible au tour près est sans espoir.")


if __name__ == "__main__":
    sys.exit(main() or 0)
