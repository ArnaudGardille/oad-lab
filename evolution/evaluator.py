"""Évaluateur OpenEvolve pour oad-lab.

Le programme évolué est le strategy.js du bot `candidate` (fork de
Petra + couche stratégie, chantier D). À chaque évaluation : on
installe le fichier dans le mod, on joue un batch de parties contre le
pool via le harnais, et on rend les métriques (combined_score =
moyenne des winrates par adversaire, parties sans résultat comptées
comme défaites).

Le pool d'adversaires = ancres Petra + hall of fame si runs/hof.json
existe (voir scripts/make_hof.py) : 3 ou 6 adversaires.

Cascade :
- stage 1 : 1 seed par adversaire (6-12 parties, ~1-2 min) — élimine
  vite les configs qui cassent le bot (elles perdent tout).
- stage 2 : évaluation complète (4 seeds, 24-48 parties, ~2-8 min).

parallel_evaluations DOIT rester à 1 : l'évaluateur écrit dans le même
dossier candidate/ et le parallélisme est déjà au niveau des parties.
"""

import fcntl
import hashlib
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "harness") not in sys.path:
    sys.path.insert(0, str(REPO / "harness"))

from oadlab import evalapi  # noqa: E402

# Depuis le chantier D (SPEC.md §3 bis), le programme évolué est la
# COUCHE STRATÉGIE (strategy.js) : Petra relit sa Config en continu,
# et strategy.update() peut la réécrire à chaque tour selon l'état du
# jeu — config.js reste la baseline vanilla. L'installation détecte le
# substrat du programme : les runs de l'ère config.js restent évalués
# correctement (OpenEvolve ré-importe ce module à CHAQUE évaluation —
# un changement ici prend effet au milieu d'un run en cours).
CANDIDATE_DIR = REPO / "bots/oadlab/simulation/ai/candidate"
CANDIDATE_STRATEGY = CANDIDATE_DIR / "strategy.js"
CANDIDATE_CONFIG = CANDIDATE_DIR / "config.js"

INSTALL_LOCK = REPO / "runs" / ".candidate_install.lock"


@contextmanager
def _install_lock():
    """Verrou exclusif autour de l'installation + l'évaluation du
    candidate : deux évaluations concurrentes écriraient dans le même
    config.js et corrompraient silencieusement l'attribution des
    scores (parallel_evaluations DOIT rester à 1, mais ceci le rend
    infranchissable plutôt que documentaire)."""
    INSTALL_LOCK.parent.mkdir(parents=True, exist_ok=True)
    lockf = open(INSTALL_LOCK, "w")
    try:
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "une autre évaluation candidate tourne déjà "
                f"(verrou {INSTALL_LOCK}) — refus d'écraser config.js")
        yield
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()


def _install(program_path):
    if not CANDIDATE_DIR.exists():
        raise RuntimeError(
            "bot candidate absent — lancer scripts/make_candidate.py")
    code = Path(program_path).read_text(errors="replace")
    target = CANDIDATE_STRATEGY if "export function Strategy" in code \
        else CANDIDATE_CONFIG
    shutil.copyfile(program_path, target)


def _tag(program_path):
    """Tag = hash du CONTENU, pas du chemin : OpenEvolve évalue via un
    fichier temporaire anonyme, seul le code relie les matchs en base
    au programme du DAG (export_lineage calcule le même hash)."""
    code = Path(program_path).read_bytes()
    return f"cand-{hashlib.sha1(code).hexdigest()[:12]}"


def evaluate_stage1(program_path):
    with _install_lock():
        _install(program_path)
        return evalapi.evaluate_bot("candidate", pairs=1,
                                    tag=_tag(program_path) + "-s1",
                                    log=lambda *a: None)


def evaluate_stage2(program_path):
    with _install_lock():
        _install(program_path)
        return evalapi.evaluate_bot("candidate", pairs=4,
                                    tag=_tag(program_path),
                                    log=lambda *a: None)


def evaluate(program_path):
    return evaluate_stage2(program_path)
