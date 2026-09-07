#!/usr/bin/env python3
"""Matérialise un hall of fame depuis un checkpoint OpenEvolve.

Sélectionne N élites DIVERSES : les programmes sont parcourus par score
décroissant et un programme n'est retenu que s'il occupe une cellule
comportementale (aggression, boom) encore libre — le hall of fame doit
couvrir des styles, pas cloner N fois le meilleur. Pour les checkpoints
antérieurs aux descripteurs (métriques sans aggression/boom), on retombe
sur une déduplication par lignée (parent_id) : grossier mais mieux que
rien.

Chaque élite devient un bot `hofK` (fork de Petra + son config.js,
chemins réécrits), et le manifeste runs/hof.json est écrit ; dès qu'il
existe, le harnais (oadlab.evalapi) ajoute ces bots au pool
d'évaluation. Le combined_score change alors d'échelle — c'est voulu
(cible mobile, façon league play) ; les scores avec et sans hall of
fame ne se comparent pas.

N'entrent au hall of fame que des programmes dont le score a été
CONFIRMÉ (stage 3 : rejoués sur CONFIRM_SEEDS, disjoints des seeds qui
les ont sélectionnés). Sans cela on promeut la malédiction du
vainqueur : le score qui fait entrer un programme est le tirage même
qui l'a fait gagner, et le hall of fame gèle du bruit en cible mobile
— c'est ce qui s'est passé les 2026-09-02/03, où les trois hofN
promus se sont révélés à égalité stricte (49,2 %) avec la population
qu'ils étaient censés surpasser. `--allow-unconfirmed` rétablit
l'ancien comportement pour les checkpoints antérieurs au stage 3.

Usage : scripts/make_hof.py <checkpoint> [n] [--allow-unconfirmed]
"""

import json
import re
import shutil
import sys
from pathlib import Path

from make_candidate import AI_DIR, REPO, make_bot

HOF_CELLS = 4  # grille (aggression x boom) 4x4 pour la déduplication


def _normalize(code):
    """Forme fonctionnelle du code : commentaires et espaces retirés.
    Deux programmes qui ne diffèrent que par leurs commentaires (le LLM
    reformule volontiers les siens) sont le même bot."""
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    return re.sub(r"\s+", "", code)


# Part maximale de parties sans résultat dans une confirmation. Une
# élite dont un quart des parties fige le moteur n'est pas une élite :
# sans ce plancher, 96 parties toutes sans résultat donneraient un
# score de 0 assorti du label « confirmé ».
MAX_NO_RESULT = 0.25


def score_of(p, confirmed):
    """Le score qui fait foi : confirmé s'il existe, sinon celui de
    sélection. Les deux ne vivent pas sur le même axe (protocoles
    distincts, P3) — d'où le paramètre plutôt qu'un `or`."""
    m = p.get("metrics") or {}
    return m.get("confirmed_score") if confirmed else m.get("combined_score")


def pick_elites(programs, n, require_confirmed=True):
    def eligible(p):
        m = p.get("metrics") or {}
        if score_of(p, require_confirmed) is None:
            return False
        if not require_confirmed:
            return True
        # `confirmed_*` n'est posé que par evaluate_stage3 : sa présence
        # atteste un score joué sur des seeds FRAÎCHES, disjointes de
        # celles qui ont sélectionné le programme. Il ne remplace pas
        # le combined_score (qui reste la fitness d'OpenEvolve) : c'est
        # ici, à la promotion, qu'il fait foi.
        games = m.get("confirmed_games", 0)
        if games <= 0 or m["confirmed_score"] <= 0:
            return False
        return m.get("confirmed_no_result", 0) <= MAX_NO_RESULT * games

    ranked = sorted((p for p in programs if eligible(p)),
                    key=lambda p: -score_of(p, require_confirmed))
    seen, seen_code, elites = set(), set(), []
    for p in ranked:
        m = p["metrics"]
        # Dédup FONCTIONNELLE : les migrations inter-îlots d'OpenEvolve
        # copient un programme sous un nouvel id, et le LLM produit des
        # variantes qui ne diffèrent que par les commentaires. Sans
        # ceci, le hall of fame contient plusieurs fois le même bot
        # (constaté au premier essai : hof1 == hof2, parties rejouées
        # à l'identique).
        norm = _normalize(p["code"])
        if norm in seen_code:
            continue
        seen_code.add(norm)
        if "aggression" in m and "boom" in m:
            cell = (min(HOF_CELLS - 1, int(m["aggression"] * HOF_CELLS)),
                    min(HOF_CELLS - 1, int(m["boom"] * HOF_CELLS)))
        else:
            cell = ("lineage", p.get("parent_id"))
        if cell in seen:
            continue
        seen.add(cell)
        elites.append(p)
        if len(elites) >= n:
            break
    return elites


def main():
    args = [a for a in sys.argv[1:] if a != "--allow-unconfirmed"]
    require_confirmed = "--allow-unconfirmed" not in sys.argv
    if len(args) not in (1, 2):
        sys.exit(__doc__)
    ckpt = Path(args[0]).resolve()
    n = int(args[1]) if len(args) == 2 else 3
    programs_dir = ckpt / "programs"
    if not programs_dir.is_dir():
        sys.exit(f"pas de dossier programs/ dans {ckpt}")
    programs = [json.loads(f.read_text())
                for f in sorted(programs_dir.glob("*.json"))]
    elites = pick_elites(programs, n, require_confirmed)
    if not elites and require_confirmed:
        sys.exit(
            "aucun programme CONFIRMÉ dans ce checkpoint : le hall of "
            "fame ne promeut que des scores rejoués sur seeds frais "
            "(stage 3). Aucun candidat n'a franchi la porte de confirmation "
            "(evaluator._confirm_gate), "
            "ou ce checkpoint est antérieur au stage 3 — dans ce cas "
            "--allow-unconfirmed, en sachant qu'on promeut alors un "
            "maximum de tirages et non une force démontrée.")
    if not elites:
        sys.exit("aucun programme avec combined_score dans ce checkpoint")

    # Construction en deux temps : tout le lot est d'abord bâti dans
    # des dossiers de staging, puis publié (rename) et le manifeste
    # écrit — un échec à mi-course ne laisse ni bots hofK à moitié
    # remplacés ni manifeste désynchronisé des bots présents.
    manifest, staged = [], []
    try:
        for i, p in enumerate(elites, 1):
            name = f"hof{i}"
            score = score_of(p, require_confirmed)
            stage = AI_DIR / f".stage-{name}"
            make_bot(name, f"Hall of fame oad-lab #{i} — programme "
                     f"{p['id'][:8]} (score {score:.3f}).", dest=stage)
            code = p["code"].replace("simulation/ai/candidate/",
                                     f"simulation/ai/{name}/")
            # Substrat du programme : couche stratégie (chantier D) ou
            # config.js (ère antérieure) — les deux restent jouables.
            target = "strategy.js" if "export function Strategy" in code \
                else "config.js"
            (stage / target).write_text(code)
            staged.append((stage, AI_DIR / name))
            manifest.append({"bot": name, "program_id": p["id"],
                             "combined_score": score,
                             "confirmed": bool(require_confirmed),
                             "confirmed_games":
                                 (p.get("metrics") or {}).get(
                                     "confirmed_games"),
                             "aggression": p["metrics"].get("aggression"),
                             "boom": p["metrics"].get("boom"),
                             "checkpoint": str(ckpt.relative_to(REPO)
                                               if ckpt.is_relative_to(REPO)
                                               else ckpt)})
            print(f"{name}: {p['id'][:8]} score {score:.3f}")
    except BaseException:
        for stage, _ in staged:
            shutil.rmtree(stage, ignore_errors=True)
        raise

    for stage, final in staged:
        if final.exists():
            shutil.rmtree(final)
        stage.rename(final)
    (REPO / "runs").mkdir(exist_ok=True)
    (REPO / "runs/hof.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"manifeste écrit : runs/hof.json ({len(manifest)} bots — "
          "le pool d'évaluation les inclut désormais)")


if __name__ == "__main__":
    main()
