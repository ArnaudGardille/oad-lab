"""Winrates avec intervalle de Wilson, et rating openskill si disponible."""

import math


def wilson(k, n, z=1.96):
    """Intervalle de confiance à 95% sur une proportion k/n."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


# Ancrage arbitraire mais stable : mu des ancres par difficulté. Seul le
# candidat évolue ; les ancres sont réinitialisées à chaque match.
# sigma=1.0 (bien sous le défaut ~8.3) : les ancres sont des étalons,
# pas des joueurs incertains — le rating du candidat converge plus vite
# et ne dérive pas avec l'ordre des matchs.
ANCHOR_MU = {2: 20.0, 3: 25.0, 4: 30.0}


def openskill_rating(match_rows):
    """Rating du candidat contre ancres épinglées ; None si openskill
    n'est pas installé ou si aucun match décisif."""
    try:
        from openskill.models import PlackettLuce
    except ImportError:
        return None
    model = PlackettLuce()
    cand = model.rating(name="candidate")
    played = 0
    for row in match_rows:
        anchor = model.rating(mu=ANCHOR_MU.get(row["opp_diff"], 25.0),
                              sigma=1.0, name="anchor")
        ranks = [0, 1] if row["cand_won"] else [1, 0]
        [[cand], _] = model.rate([[cand], [anchor]], ranks=ranks)
        played += 1
    if not played:
        return None
    return {"mu": cand.mu, "sigma": cand.sigma,
            "ordinal": cand.ordinal(), "games": played}
