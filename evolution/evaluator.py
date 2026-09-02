"""Évaluateur OpenEvolve pour oad-lab.

Le programme évolué est le config.js du bot `candidate` (fork de
Petra). À chaque évaluation : on installe le fichier dans le mod, on
joue un batch de parties contre les ancres Petra via le harnais, et on
rend les métriques (combined_score = moyenne des winrates par ancre,
parties sans résultat comptées comme défaites).

Cascade :
- stage 1 : 1 seed par ancre (6 parties, ~1 min) — élimine vite les
  configs qui cassent le bot (elles perdent tout).
- stage 2 : évaluation complète (4 seeds, 24 parties, ~2-4 min).

parallel_evaluations DOIT rester à 1 : l'évaluateur écrit dans le même
dossier candidate/ et le parallélisme est déjà au niveau des parties.
"""

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "harness") not in sys.path:
    sys.path.insert(0, str(REPO / "harness"))

from oadlab import evalapi  # noqa: E402

CANDIDATE_CONFIG = REPO / "bots/oadlab/simulation/ai/candidate/config.js"


def _install(program_path):
    if not CANDIDATE_CONFIG.parent.exists():
        raise RuntimeError(
            "bot candidate absent — lancer scripts/make_candidate.py")
    shutil.copyfile(program_path, CANDIDATE_CONFIG)


def _tag(program_path):
    return f"cand-{Path(program_path).stem}"


def evaluate_stage1(program_path):
    _install(program_path)
    return evalapi.evaluate_bot("candidate", pairs=1,
                                tag=_tag(program_path) + "-s1",
                                log=lambda *a: None)


def evaluate_stage2(program_path):
    _install(program_path)
    return evalapi.evaluate_bot("candidate", pairs=4,
                                tag=_tag(program_path),
                                log=lambda *a: None)


def evaluate(program_path):
    return evaluate_stage2(program_path)
