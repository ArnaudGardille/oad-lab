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
- stage 0 (gratuit) : le contrat sémantique — en-tête HYPOTHESIS /
  PREDICTION — est vérifié AVANT de jouer la moindre partie. Un
  programme sans en-tête est rejeté à coût nul, avec un artefact qui
  dit pourquoi (OpenEvolve le réinjecte dans le prompt suivant).
- stage 1 : 1 seed par adversaire (6-12 parties, ~1-2 min) — élimine
  vite les configs qui cassent le bot (elles perdent tout).
- stage 2 : évaluation complète (4 seeds, 24-48 parties, ~2-8 min).
- stage 3 : CONFIRMATION des seules élites, sur CONFIRM_SEEDS —
  disjoint d'EVAL_SEEDS (16 seeds : 96 parties sur les 3 ancres,
  192 sur un pool de 6, soit ~12 à 25 min). Sans lui, le
  score qui promeut un programme est le tirage même qui l'a fait
  gagner : mesuré le 2026-09-04, sous l'hypothèse « tous les
  programmes valent le baseline », le maximum attendu de 60 tirages à
  24 parties vaut 0,77 — au-dessus du meilleur score jamais observé.
  Il rend des métriques `confirmed_*` qui n'écrasent RIEN : la
  fitness de sélection d'OpenEvolve reste le score du stage 2 (sans
  quoi les élites confirmées se font évincer par des scores jamais
  rejoués), et le score confirmé est ce qu'on publie.

parallel_evaluations DOIT rester à 1 : l'évaluateur écrit dans le même
dossier candidate/ et le parallélisme est déjà au niveau des parties.
"""

import fcntl
import hashlib
import re
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "harness") not in sys.path:
    sys.path.insert(0, str(REPO / "harness"))

from oadlab import config as harness_config  # noqa: E402
from oadlab import evalapi  # noqa: E402

try:
    from openevolve.evaluation_result import EvaluationResult
except ImportError:      # évaluateur utilisable hors OpenEvolve
    EvaluationResult = None

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

# Cascade : le stage 2 joue 4 seeds par adversaire ; la porte du stage
# 3 se calcule sur la population déjà mesurée sous CE protocole-là.
STAGE2_PAIRS = 4
CONFIRM_QUANTILE = 0.9      # on confirme le dernier décile, pas plus
CONFIRM_MIN_PROGRAMS = 12   # en-deçà, un quantile ne veut rien dire
CONFIRM_FLOOR = 0.5         # et jamais un programme qui perd son pool


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


_HYPOTHESIS = re.compile(r"^\s*//\s*HYPOTHESIS:\s*(\S.*)$", re.M | re.I)
_PREDICTION = re.compile(r"^\s*//\s*PREDICTION:\s*(\S.*)$", re.M | re.I)

# Zone d'en-tête : le contrat doit être EN TÊTE, mais le prompt montre
# lui-même une hypothèse qui déborde sur une ligne de continuation —
# exiger « les deux premières lignes » au pied de la lettre rejetterait
# des en-têtes bien formés. Dix lignes laissent la place au repli tout
# en interdisant qu'un contrat migre en milieu de fichier au fil des
# diffs (sans borne, un en-tête ligne 40 passait).
_HEADER_ZONE = 10

# Descripteurs d'un programme REJETÉ. Ce ne sont pas des mesures — il
# n'a pas été joué — mais MAP-Elites exige ses feature_dimensions pour
# tout programme ajouté. On les pose au centroïde mesuré de la
# population (aggression 0,64 / boom 0,25 sur 3712 matchs au
# 2026-09-04) et surtout PAS à 0 : OpenEvolve met les features à
# l'échelle par min-max sur un min/max courants qui ne redescendent
# jamais, donc un seul rejet à 0,0 écraserait définitivement toute la
# population dans les derniers bins de la grille — déjà effondrée à
# 5 cellules occupées sur 64. Un placeholder au centre n'apprend rien
# à personne : exactement ce qu'on veut d'un programme non joué.
_PLACEHOLDER_DESC = {"aggression": 0.64, "boom": 0.25,
                     "military": 0.0, "map_control": 0.0}

# Métriques rendues par un rejet : combined_score nul, donc sous
# cascade_thresholds[0] — le programme est écarté par la cascade.
_REJECTED = {"combined_score": 0.0, "games": 0.0, "no_result": 0.0,
             "wr_easy": 0.0, "wr_medium": 0.0, "wr_hard": 0.0,
             **_PLACEHOLDER_DESC}


def check_contract(code):
    """Rend None si le programme porte son contrat expérimental, sinon
    le motif du rejet.

    Le contrat (SPEC.md, « le contenu doit être sémantique ») veut que
    chaque mutation déclare son hypothèse et sa prédiction EN TÊTE DU
    CODE, pour voyager avec lui. En évolution par diff, rien ne le
    garantit : la première mutation du run du 2026-09-03 a supprimé
    l'en-tête du fichier initial et TOUTE la lignée en a hérité —
    0/60 programmes du checkpoint 70 le portaient, donc aucun verdict
    n'a jamais pu être calculé. On le vérifie donc ici, avant de
    dépenser la moindre partie : le coût d'un rejet est nul et
    l'artefact renvoyé explique la faute au générateur."""
    header = "\n".join(code.splitlines()[:_HEADER_ZONE])
    missing = []
    if not _HYPOTHESIS.search(header):
        missing.append("// HYPOTHESIS: <l'idée stratégique testée>")
    if not _PREDICTION.search(header):
        missing.append("// PREDICTION: <affirmations directionnelles, "
                       'p. ex. "aggression=+, boom=-, wr_hard=+">')
    if missing:
        return ("en-tête expérimental absent — le programme n'a PAS été "
                f"évalué (aucune partie jouée). Dans les {_HEADER_ZONE} "
                "premières lignes du fichier, en tête, il faut :\n"
                + "\n".join(missing))
    return None


def _reject(reason):
    if EvaluationResult is None:
        return dict(_REJECTED)
    return EvaluationResult(metrics=dict(_REJECTED),
                            artifacts={"contract_violation": reason})


def evaluate_stage1(program_path):
    reason = check_contract(Path(program_path).read_text(errors="replace"))
    if reason:
        return _reject(reason)
    with _install_lock():
        _install(program_path)
        return evalapi.evaluate_bot("candidate", pairs=1,
                                    tag=_tag(program_path) + "-s1",
                                    log=lambda *a: None)


def evaluate_stage2(program_path):
    with _install_lock():
        _install(program_path)
        return evalapi.evaluate_bot("candidate", pairs=STAGE2_PAIRS,
                                    tag=_tag(program_path),
                                    log=lambda *a: None)


def _confirm_place(scores, tag):
    """Le programme `tag` mérite-t-il ses 96 à 192 parties ? Rend
    (nombre de programmes au moins aussi bons, quota) si oui, None
    sinon.

    On raisonne en RANG, pas en valeur seuil. Trois pièges, tous
    mesurés sur la base du dépôt :

    - le combined_score change d'échelle avec le pool (médiane 0,58
      contre 3 ancres, 0,52 contre 6 où le record est 0,60), donc un
      seuil câblé confirme ~36 % des programmes ici et aucun là ;
    - à 24 parties la granularité du score est 1/24 : les ex aequo au
      décile sont la règle, et un seuil de valeur les laisse TOUS
      passer — population plate, tout le monde est confirmé. On compte
      donc les programmes au moins aussi bons (le candidat exclu) et on
      exige qu'ils tiennent dans le quota ;
    - tant que la population est trop maigre il n'y a pas de décile :
      la porte reste FERMÉE plutôt que de retomber sur un absolu — un
      repli à 0,5 laissait passer 89 % des programmes du pool à 3
      ancres, soit une dizaine de confirmations gaspillées à chaque
      changement de protocole (et le protocole change dès qu'on touche
      au pool, aux seeds ou au moteur).

    CONFIRM_FLOOR reste, lui, un nombre absolu assumé : il ne calibre
    rien, il refuse seulement de payer une confirmation à un programme
    qui perd contre son propre pool."""
    score = scores.get(tag)
    n = len(scores)
    if score is None or n < CONFIRM_MIN_PROGRAMS or score < CONFIRM_FLOOR:
        return None
    rivals = sum(1 for cand, v in scores.items() if cand != tag and v >= score)
    quota = max(1, round((1 - CONFIRM_QUANTILE) * n))
    return (rivals, quota) if rivals < quota else None


def evaluate_stage3(program_path):
    """Confirmation d'une élite sur des parties fraîches : seeds
    disjoints de ceux qui l'ont sélectionnée. Tag distinct (-s3) et
    protocole distinct (les seeds entrent dans protocol_id) — un score
    confirmé ne se mélange jamais à un score de sélection.

    Ce que ce stage rend est PRÉFIXÉ `confirmed_*` et n'écrase donc
    aucune métrique du stage 2. C'est délibéré : OpenEvolve se sert de
    `combined_score` comme fitness (database._is_better →
    get_fitness_score), pour le remplacement de cellule MAP-Elites,
    l'élagage de population et la suppression des orphelins. Écraser
    cette valeur par le score confirmé — plus bas, puisque la
    régression vers la moyenne est précisément ce qu'on mesure —
    faisait évincer les élites CONFIRMÉES par des scores de stage 2
    jamais rejoués : l'inverse du but, et deux protocoles comparés sur
    le même axe (P3). Le score de sélection reste donc la fitness ; le
    score confirmé est ce qu'on PUBLIE (make_hof.py, atelier)."""
    tag = _tag(program_path)
    pool = evalapi.opponents()
    selection_proto = evalapi.protocol_id(
        pool, harness_config.EVAL_SEEDS[:STAGE2_PAIRS])
    scores = evalapi.scores_under(selection_proto)
    place = _confirm_place(scores, tag)
    if place is None:
        # Porte fermée : aucune partie jouée, et AUCUNE métrique rendue.
        # Rendre un `confirmed_games: 0.0` serait pire que rien : le
        # prompt d'OpenEvolve transforme toute métrique <= 0,3 d'un
        # programme d'inspiration en « consider an alternative approach
        # to confirmed_games » (prompt/sampler.py), soit un conseil
        # absurde injecté dans presque chaque génération.
        return {}
    rivals, quota = place
    with _install_lock():
        _install(program_path)
        m = evalapi.evaluate_bot(
            "candidate", tag=tag + "-s3",
            seeds=harness_config.CONFIRM_SEEDS, log=lambda *a: None)
    return {"confirmed_score": m["combined_score"],
            "confirmed_games": float(m["games"]),
            "confirmed_no_result": float(m["no_result"]),
            # Le score qui a ouvert la porte : agrégé sur TOUTES les
            # parties de ce code sous le protocole de sélection, donc
            # pas forcément égal au combined_score du dernier batch
            # (un code réévalué est moyenné sur ses batchs).
            "pooled_selection_score": scores[tag],
            "confirm_rivals": float(rivals),
            "confirm_quota": float(quota)}


def evaluate(program_path):
    """Repli non-cascade : n'existe QUE pour un appelant qui n'a pas
    cascade_evaluation=true (config.yaml l'a — ce chemin est dormant en
    usage normal). evaluate_stage1 est le seul autre point d'entrée
    protégé par check_contract ; sans le rappeler ici, un appel direct
    à evaluate() jouerait des parties pour un programme sans en-tête,
    contredisant la garantie documentée en tête de ce module."""
    reason = check_contract(Path(program_path).read_text(errors="replace"))
    if reason:
        return _reject(reason)
    return evaluate_stage2(program_path)
