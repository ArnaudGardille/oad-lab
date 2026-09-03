#!/usr/bin/env python3
"""(Re)génère un bot dérivé de `forkbot`.

En CLI : régénère le bot `candidate`, celui que la boucle d'évolution
modifie (elle écrase son config.js à chaque évaluation). Le dossier est
gitignoré (état généré) ; ce script le reconstruit depuis forkbot.

`make_bot` est aussi importé par make_hof.py pour matérialiser les
membres du hall of fame (mêmes règles de réécriture des chemins).
"""

import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FORK = REPO / "bots/oadlab/simulation/ai/forkbot"
AI_DIR = REPO / "bots/oadlab/simulation/ai"


def make_bot(name, description, dest=None):
    """Copie forkbot vers `dest` (par défaut bots/.../ai/<name>/) en
    réécrivant les chemins d'import pour `name`. Rend le dossier créé.
    `dest` permet de construire ailleurs puis publier par rename
    (make_hof.py) : les imports référencent le nom final, pas
    l'emplacement de construction."""
    dest = dest or AI_DIR / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(FORK, dest)
    for f in dest.glob("*.js"):
        f.write_text(f.read_text().replace(
            "simulation/ai/forkbot/", f"simulation/ai/{name}/"))
    data = json.loads((dest / "data.json").read_text())
    data["name"] = name.capitalize()
    data["description"] = description
    (dest / "data.json").write_text(json.dumps(data, indent="\t") + "\n")
    return dest


def main():
    dest = make_bot("candidate",
                    "Bot en cours d'évolution (oad-lab) — config.js est "
                    "écrasé par la boucle d'évolution.")
    print(f"candidate régénéré depuis forkbot : {dest}")


if __name__ == "__main__":
    main()
