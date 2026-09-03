"""Descripteurs comportementaux d'une partie, extraits des séries
temporelles de metadata.json (StatisticsTracker du moteur, un
échantillon toutes les 30 s de temps de jeu).

Pourquoi metadata.json et pas commands.txt : en partie 100 % IA,
commands.txt ne contient AUCUNE commande — les IA postent leurs ordres
directement dans la simulation, rien ne transite par la couche réseau
que le replay enregistre. Les séries du StatisticsTracker sont la
seule trace comportementale disponible sans instrumenter le moteur.

Tous les descripteurs sont dans [0, 1]. OpenEvolve rescale de toute
façon (minmax adaptatif), mais des bornes fixes gardent les valeurs
comparables d'un run à l'autre et lisibles en base.
"""

import json

# Bornes de normalisation (parties mainland 128 observées : premier
# sang typique entre 5 et 18 min ; pop à 10 min entre ~40 et ~180).
AGGRO_HORIZON_S = 1200.0   # premier sang à t=0 -> 1 ; à 20 min ou jamais -> 0
BOOM_SAMPLE_S = 600.0      # la population est lue à 10 min de jeu
BOOM_POP_MAX = 200.0


def _clamp(x):
    return max(0.0, min(1.0, x))


def _first_positive(times, values):
    for t, v in zip(times, values):
        if v and v > 0:
            return t
    return None


def _value_at(times, values, target):
    """Dernier échantillon dont l'horodatage est <= target."""
    best = values[0] if values else 0
    for t, v in zip(times, values):
        if t > target:
            break
        best = v
    return best


def from_metadata(meta_path, cand_pos):
    """Descripteurs du joueur `cand_pos` (1 ou 2, gaia occupant l'index
    0 de playerStates). None si le metadata est absent, tronqué (SIGKILL
    en pleine écriture) ou sans séries."""
    try:
        m = json.loads(meta_path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    states = m.get("playerStates", [])
    if len(states) <= cand_pos:
        return None
    seq = states[cand_pos].get("sequences") or {}
    times = seq.get("time")
    if not times:
        return None

    def series(*path):
        v = seq
        for k in path:
            v = v.get(k) if isinstance(v, dict) else None
            if v is None:
                return []
        return v if isinstance(v, list) else []

    out = {}

    # Agressivité : précocité du premier sang INFLIGÉ (la valeur des
    # unités ennemies tuées, pas nos pertes : subir un rush n'est pas
    # être agressif).
    first_blood = _first_positive(times, series("enemyUnitsKilledValue"))
    out["aggression"] = max(0.0, 1.0 - first_blood / AGGRO_HORIZON_S) \
        if first_blood is not None else 0.0

    # Boom : vitesse de montée en population, lue à 10 min.
    pop = series("populationCount")
    out["boom"] = min(1.0, _value_at(times, pop, BOOM_SAMPLE_S)
                      / BOOM_POP_MAX) if pop else 0.0

    # Part militaire de la production. Les citoyens-soldats comptent
    # comme Worker mais pas comme Civilian : 1 - Civilian/total mesure
    # bien la part combattante.
    trained = series("unitsTrained", "total")
    civilian = series("unitsTrained", "Civilian")
    out["military"] = _clamp(
        1.0 - (civilian[-1] if civilian else 0) / trained[-1]) \
        if trained and trained[-1] else 0.0

    # Emprise territoriale maximale atteinte.
    control = series("peakPercentMapControlled")
    out["map_control"] = _clamp(control[-1] / 100.0) if control else 0.0

    return out
