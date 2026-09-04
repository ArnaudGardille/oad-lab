#!/usr/bin/env python3
"""Importe le DAG des lignées d'un checkpoint OpenEvolve dans la base
SQLite du harnais (table `programs`) — la même base que les matchs,
celle que l'atelier visuel (phase 6) lira.

Usage : scripts/export_lineage.py runs/evolution/<run>/checkpoints/checkpoint_N

Idempotent (INSERT OR REPLACE sur l'id du programme) : réimporter un
checkpoint plus récent du même run met les lignées à jour.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
from oadlab import db  # noqa: E402

# Seuil par dimension au-delà duquel un delta compte comme un vrai
# mouvement (sous le seuil : « = », effet nul). Les winrates de stage 2
# sont mesurés sur 8 parties par adversaire : leur pas est 0,125 et le
# bruit binomial ~0,18 — en dessous de 0,25 (2 parties), tout est du
# bruit. Les descripteurs sont des moyennes continues : 0,05 suffit.
EFFECT_MIN = {"aggression": 0.05, "boom": 0.05, "military": 0.05,
              "map_control": 0.05}
EFFECT_MIN_DEFAULT = 0.25   # wr_*, combined_score

_HYP = re.compile(r"^//\s*HYPOTHESIS:\s*(.+)$", re.M | re.I)
_PRED = re.compile(r"^//\s*PREDICTION:\s*(.+)$", re.M | re.I)
_CLAIM = re.compile(r"(aggression|boom|military|map_control|wr_easy"
                    r"|wr_medium|wr_hard|wr_hof|combined_score)"
                    r"\s*=\s*([+\-=])", re.I)


def parse_header(code):
    """Extrait (hypothèse, prédiction) de l'en-tête du programme —
    le contrat imposé par le system_message de l'évolution."""
    hyp = _HYP.search(code or "")
    pred = _PRED.search(code or "")
    return (hyp.group(1).strip() if hyp else None,
            pred.group(1).strip() if pred else None)


def verdict(prediction, child_m, parent_m):
    """Confronte chaque affirmation directionnelle à la mesure
    (delta enfant - parent). Rend None sans prédiction exploitable."""
    if not prediction or not parent_m:
        return None
    checks = []
    for dim, direction in _CLAIM.findall(prediction):
        dim = dim.lower()
        c, p = child_m.get(dim), parent_m.get(dim)
        if c is None or p is None:
            continue
        delta = c - p
        threshold = EFFECT_MIN.get(dim, EFFECT_MIN_DEFAULT)
        measured = "+" if delta > threshold else \
                   "-" if delta < -threshold else "="
        ok = measured == direction
        checks.append((dim, direction, measured, ok))
    if not checks:
        return None
    n_ok = sum(1 for *_, ok in checks if ok)
    detail = ", ".join(
        f"{dim}{direction}{'✓' if ok else f'✗({measured})'}"
        for dim, direction, measured, ok in checks)
    return f"{n_ok}/{len(checks)} confirmées — {detail}"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    ckpt = Path(sys.argv[1]).resolve()
    programs_dir = ckpt / "programs"
    if not programs_dir.is_dir():
        sys.exit(f"pas de dossier programs/ dans {ckpt}")
    # runs/evolution/<run>/checkpoints/checkpoint_N -> <run>
    run = ckpt.parent.parent.name

    programs = [json.loads(f.read_text())
                for f in sorted(programs_dir.glob("*.json"))]
    by_id = {p["id"]: p for p in programs}

    con = db.connect()
    for p in programs:
        meta = p.get("metadata") if isinstance(p.get("metadata"), dict) \
            else {}
        code = p.get("code") or ""
        parent = by_id.get(p.get("parent_id"))
        pid8 = (p.get("parent_id") or "?")[:8]
        if meta.get("migrant"):
            # Clone de migration inter-îlots : OpenEvolve copie code,
            # métriques ET résumé de changements de l'original. Importé
            # tel quel, le nœud ressemble à une mutation dont le delta
            # est exactement 0.0 partout — c'est l'« anomalie confirmée
            # 9x » du carnet (nuit 2026-09-03). On remplace le matériau
            # hérité par un marquage explicite.
            hyp = pred = None
            changes = (f"{db.MIGRATION_PREFIX} copie conforme de {pid8}"
                       f" vers l'îlot {meta.get('island', '?')} —"
                       " pas une mutation")
            vd = f"migration — copie de {pid8}, non-expérience"
        else:
            hyp, pred = parse_header(code)
            changes = meta.get("changes")
            if parent is None and p.get("parent_id"):
                # Parent élagué du checkpoint : retomber sur la base,
                # qu'un import précédent a peut-être garnie — sans quoi
                # ni le verdict ni la détection de no-op ne le voient.
                row = con.execute(
                    "SELECT code, combined_score, wr_easy, wr_medium,"
                    " wr_hard, aggression, boom FROM programs"
                    " WHERE id = ?", (p["parent_id"],)).fetchone()
                if row:
                    parent = {"code": row["code"],
                              "metrics": {k: row[k] for k in row.keys()
                                          if k != "code"}}
            if parent and (parent.get("code") or "") == code:
                # apply_diff d'OpenEvolve ignore en silence un bloc
                # SEARCH sans correspondance exacte : l'enfant garde le
                # code du parent et la mutation décrite dans `changes`
                # n'a jamais été jouée — un verdict directionnel serait
                # un mensonge.
                vd = ("diff non appliqué (SEARCH sans correspondance) —"
                      f" code identique au parent {pid8}, mutation"
                      " jamais testée")
            else:
                vd = verdict(pred, p.get("metrics") or {},
                             (parent or {}).get("metrics"))
        db.upsert_program(
            con, id=p["id"], run=run, parent_id=p.get("parent_id"),
            generation=p.get("generation"),
            iteration=p.get("iteration_found"), ts=p.get("timestamp"),
            metrics=p.get("metrics"),
            changes=changes,
            code=code,
            code_sha=hashlib.sha1(code.encode()).hexdigest()[:12],
            hypothesis=hyp, prediction=pred,
            verdict=vd)
    con.commit()
    con.close()
    print(f"{len(programs)} programmes importés (run {run})")


if __name__ == "__main__":
    main()
