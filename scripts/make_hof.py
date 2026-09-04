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

Usage : scripts/make_hof.py runs/evolution/<run>/checkpoints/checkpoint_N [n]
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


def pick_elites(programs, n):
    ranked = sorted(
        (p for p in programs
         if (p.get("metrics") or {}).get("combined_score") is not None),
        key=lambda p: -p["metrics"]["combined_score"])
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
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    ckpt = Path(sys.argv[1]).resolve()
    n = int(sys.argv[2]) if len(sys.argv) == 3 else 3
    programs_dir = ckpt / "programs"
    if not programs_dir.is_dir():
        sys.exit(f"pas de dossier programs/ dans {ckpt}")
    programs = [json.loads(f.read_text())
                for f in sorted(programs_dir.glob("*.json"))]
    elites = pick_elites(programs, n)
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
            score = p["metrics"]["combined_score"]
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
