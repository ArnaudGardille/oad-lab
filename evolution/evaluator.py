"""Évaluateur OpenEvolve pour oad-lab.

Le programme évolué est le config.js du bot `candidate` (fork de
Petra). À chaque évaluation : on installe le fichier dans le mod, on
joue un batch de parties contre les ancres Petra via le harnais, et on
rend les métriques (combined_score = moyenne des winrates par ancre,
parties sans résultat comptées comme défaites).

Le pool d'adversaires = ancres Petra + hall of fame si runs/hof.json
existe (voir scripts/make_hof.py) : 3 ou 6 adversaires.

Cascade :
- stage 1 : 1 seed par adversaire (6-12 parties, ~1-2 min) — élimine
  vite les configs qui cassent le bot (elles perdent tout).
- stage 2 : évaluation complète (4 seeds, 24-48 parties, ~2-8 min).

parallel_evaluations DOIT rester à 1 : l'évaluateur écrit dans le même
dossier candidate/ et le parallélisme est déjà au niveau des parties.
"""

import hashlib
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
    """Tag = hash du CONTENU, pas du chemin : OpenEvolve évalue via un
    fichier temporaire anonyme, seul le code relie les matchs en base
    au programme du DAG (export_lineage calcule le même hash)."""
    code = Path(program_path).read_bytes()
    return f"cand-{hashlib.sha1(code).hexdigest()[:12]}"


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
