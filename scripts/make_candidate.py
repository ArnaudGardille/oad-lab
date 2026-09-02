#!/usr/bin/env python3
"""(Re)génère le bot `candidate` à partir de `forkbot`.

`candidate` est le bot que la boucle d'évolution modifie : la boucle
écrase son config.js à chaque évaluation. Le dossier est gitignoré
(état généré) ; ce script le reconstruit à l'identique depuis forkbot.
"""

import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FORK = REPO / "bots/oadlab/simulation/ai/forkbot"
CAND = REPO / "bots/oadlab/simulation/ai/candidate"


def main():
    if CAND.exists():
        shutil.rmtree(CAND)
    shutil.copytree(FORK, CAND)
    for f in CAND.glob("*.js"):
        f.write_text(f.read_text().replace(
            "simulation/ai/forkbot/", "simulation/ai/candidate/"))
    data = json.loads((CAND / "data.json").read_text())
    data["name"] = "Candidate"
    data["description"] = "Bot en cours d'évolution (oad-lab) — " \
        "config.js est écrasé par la boucle d'évolution."
    (CAND / "data.json").write_text(json.dumps(data, indent="\t") + "\n")
    print(f"candidate régénéré depuis forkbot : {CAND}")


if __name__ == "__main__":
    main()
