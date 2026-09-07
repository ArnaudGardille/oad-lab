#!/usr/bin/env python3
"""Chantier D, temps 2 : le hook de stratégie change-t-il le jeu ?

`_petrabot.js` appelle désormais `this.strategy.update(...)` à chaque
tour joué (SPEC.md §3 bis) ; avec la stratégie NULLE (aucun champ de
Config touché), le bot doit se comporter EXACTEMENT comme avant le
patch. « Exactement » est optimiste : `determinism_probe.py` a montré
que le moteur diverge dans 25 à 40 % des rejeux d'une partie pourtant
identique au bit près, par pur timing CPU — un simple appel de
fonction supplémentaire à chaque tour est le genre de chose qui peut
déplacer ce timing. Le hook n'est donc pas dispensé de preuve sous
prétexte qu'il ne touche à rien : on le mesure.

Méthode : deux bots identiques à un octet près — `parityhook` (le
forkbot actuel, hook + stratégie nulle) et `paritynohook` (son
_petrabot.js sans le bloc try/update, sans strategy.js) — s'affrontent
en miroir. Deux bras :

- CONTRÔLE : paritynohook contre lui-même (N parties, une par seed) —
  calibre le bruit pur, sans AUCUNE différence de code des deux côtés ;
  le winrate attendu est 50 % par symétrie, l'écart mesuré ici est
  l'échelle de bruit à laquelle comparer le bras de test.
- TEST : parityhook contre paritynohook, mirroré (chaque seed joué
  dans les deux sens) — si le hook est neutre, son winrate doit se
  fondre dans le même bruit que le bras CONTRÔLE, pas s'en écarter.

Les parties sont enregistrées en base (P1) sous les tags `parity-hook`
/ `parity-ctrl`, avec leur protocole.

Usage : scripts/parity_check.py [seeds_test] [seeds_ctrl]
        (défaut : 12 seeds test [24 parties mirrorées], 8 contrôle)
"""

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "harness"))
sys.path.insert(0, str(REPO / "scripts"))

from make_candidate import AI_DIR, make_bot  # noqa: E402
from oadlab import config, db, evalapi, game, stats  # noqa: E402

HOOK, NOHOOK = "parityhook", "paritynohook"
DIFF = config.CANDIDATE_DIFF
# Hors EVAL_SEEDS (101-106), CONFIRM_SEEDS (201-216) et le remplissage
# de determinism_probe (900+) : cette sonde ne doit polluer aucun
# protocole d'évaluation existant.
BASE_SEED = 501

# Le bloc exact du hook dans forkbot/_petrabot.js (littéral, pas une
# regex à motif : comme check_behavior_pin._ANCHOR, une chaîne figée
# rend le script sourd au silence — s'il dérive, .count() != 1 lève
# plutôt que de laisser un bot "sans hook" qui l'a encore).
_HOOK_BLOCK = (
    "\t\t// Un crash de la stratégie ne coûte que l'ajustement de CE\n"
    "\t\t// tour : Petra continue en vanilla (repli sûr, oad-lab).\n"
    "\t\ttry\n"
    "\t\t{\n"
    "\t\t\tthis.strategy.update(this.gameState, this.Config);\n"
    "\t\t}\n"
    "\t\tcatch (e)\n"
    "\t\t{\n"
    "\t\t\tif (!this.strategyWarned)\n"
    "\t\t\t{\n"
    '\t\t\t\taiWarn("oadlab strategy.update failed: " + e);\n'
    "\t\t\t\tthis.strategyWarned = true;\n"
    "\t\t\t}\n"
    "\t\t}\n"
)


def _strategy_import(name):
    return f'import {{ Strategy }} from "simulation/ai/{name}/strategy.js";\n'


_STRATEGY_NEW = "\tthis.strategy = new Strategy(this.Config);\n"


def build_bots():
    hook_dir = make_bot(HOOK, "Sonde de parité — AVEC le hook de "
                              "stratégie (stratégie nulle).")
    nohook_dir = make_bot(NOHOOK, "Sonde de parité — SANS le hook de "
                                  "stratégie (contrôle).")
    petra = nohook_dir / "_petrabot.js"
    src = petra.read_text()
    if src.count(_HOOK_BLOCK) != 1:
        raise RuntimeError(
            "bloc du hook introuvable ou dupliqué dans _petrabot.js — "
            "le patron a dérivé, mettre à jour _HOOK_BLOCK")
    stripped = src.replace(_HOOK_BLOCK, "")
    stripped = stripped.replace(_strategy_import(NOHOOK), "")
    stripped = stripped.replace(_STRATEGY_NEW, "")
    petra.write_text(stripped)
    return hook_dir, nohook_dir


def cleanup():
    for name in (HOOK, NOHOOK):
        shutil.rmtree(AI_DIR / name, ignore_errors=True)


def play_mirrored(seed, ai1, ai2):
    base = seed * 1000 + DIFF * 10
    return [game.GameSpec(ai1, ai2, DIFF, DIFF, seed, base + 1),
            game.GameSpec(ai2, ai1, DIFF, DIFF, seed, base + 2)]


def play_all(con, specs, tags, proto, log):
    """Toutes les parties en UN SEUL run_batch : c'est là qu'est la
    parallélisation (12 cœurs), pas dans une boucle Python qui
    soumettrait les parties une par une. `tags` associe chaque spec
    (par clé) à son bras, pour le log et l'attribution du résultat."""
    results = game.run_batch(specs, log=log)
    by_key = {r["spec"].key(): r for r in results}
    rows = {}
    for spec in specs:
        res = by_key[spec.key()]
        r = res["replay"]
        cand_won = turns = game_s = replay_dir = None
        if r and r["states"] and len(r["states"]) == 2:
            turns, replay_dir, game_s = r["turns"], r["dir"], r["game_s"]
            cand_won = int(r["states"][0] == "won")
        db.insert_match(con, candidate=spec.ai1, opponent=spec.ai2,
                        opp_diff=DIFF, cand_pos=1, spec=spec,
                        cand_won=cand_won, timed_out=res["timed_out"],
                        game_s=game_s, turns=turns, wall_s=res["wall_s"],
                        replay_dir=replay_dir, descriptors=None,
                        protocol=proto)
        rows[spec.key()] = cand_won
        tag = tags[spec.key()]
        print(f"  {tag} seed {spec.seed} ({spec.ai1} vs {spec.ai2}): "
              f"{'V' if cand_won == 1 else 'D' if cand_won == 0 else '∅'}")
    return rows


def main():
    n_test = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    n_ctrl = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    proto = evalapi.protocol_id([("parity", DIFF)],
                                [BASE_SEED, BASE_SEED + n_test + n_ctrl])

    ctrl_specs = [game.GameSpec(NOHOOK, NOHOOK, DIFF, DIFF,
                                BASE_SEED + i,
                                (BASE_SEED + i) * 1000 + DIFF * 10 + 1)
                 for i in range(n_ctrl)]
    test_pairs = [play_mirrored(BASE_SEED + n_ctrl + i, HOOK, NOHOOK)
                 for i in range(n_test)]
    test_specs = [s for pair in test_pairs for s in pair]
    tags = {s.key(): "parity-ctrl" for s in ctrl_specs}
    tags.update({s.key(): "parity-hook" for s in test_specs})

    print(f"construction des bots jetables {HOOK}/ et {NOHOOK}/ …")
    build_bots()
    con = db.connect()
    try:
        print(f"\n{len(ctrl_specs)} parties CONTRÔLE ({NOHOOK} contre "
              f"lui-même) + {len(test_specs)} parties TEST ({HOOK} "
              f"contre {NOHOOK}, mirrorées) — {config.PARALLEL} en "
              "parallèle, un seul lot :")
        won = play_all(con, ctrl_specs + test_specs, tags, proto, print)
    finally:
        con.close()
        cleanup()

    ctrl = [won[s.key()] for s in ctrl_specs]
    test = []
    for pair in test_pairs:
        # pair[1] a HOOK en ai2 : son résultat est celui de NOHOOK, à
        # inverser pour additionner le point de vue de HOOK.
        a, b = won[pair[0].key()], won[pair[1].key()]
        test.append(a)
        test.append(None if b is None else 1 - b)

    def summarize(label, rows):
        dec = [r for r in rows if r is not None]
        k = sum(dec)
        nores = len(rows) - len(dec)
        lo, hi = stats.wilson(k, len(dec)) if dec else (0.0, 1.0)
        print(f"\n{label} : {k}/{len(dec)} ({100 * k / len(dec):.0f} %) "
              f"IC95 [{100 * lo:.0f}-{100 * hi:.0f}%]"
              + (f" | {nores} sans résultat" if nores else ""))
        return lo, hi

    print("\n--- résultat ---")
    ctrl_lo, ctrl_hi = summarize(f"CONTRÔLE (bruit pur, attendu ~50%)", ctrl)
    test_lo, test_hi = summarize(f"TEST (winrate de {HOOK})", test)

    print(f"\nprotocole : {proto} — parties enregistrées en base "
          f"(candidate = {HOOK!r}/{NOHOOK!r})")

    overlap = max(ctrl_lo, test_lo) <= min(ctrl_hi, test_hi)
    if not test_lo <= 0.5 <= test_hi:
        print("\nATTENTION : l'IC95 du bras TEST exclut 50 % — signal "
              "possible, à confirmer sur un plus grand nombre de "
              "parties avant d'en tirer une conclusion.")
        return 1
    if not overlap:
        print("\nATTENTION : les IC95 des deux bras ne se recouvrent "
              "pas — le hook déplace peut-être le jeu au-delà du bruit "
              "de fond, à rejouer avec plus de parties avant de "
              "conclure.")
        return 1
    print("\nAucun écart détecté au-delà du bruit de fond (les deux "
          "bras sont statistiquement indissociables). Ce n'est pas une "
          "preuve d'équivalence stricte — seulement qu'un échantillon "
          f"de {n_test * 2 + n_ctrl} parties ne voit rien. Le hook peut "
          "être tenu pour neutre au niveau de précision de ce harnais.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
