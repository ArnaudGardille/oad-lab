#!/usr/bin/env python3
"""Importe le DAG des lignées d'un checkpoint OpenEvolve dans la base
SQLite du harnais (table `programs`) — la même base que les matchs,
celle que l'atelier visuel (phase 6) lira.

Usage : scripts/export_lineage.py runs/evolution/<run>/checkpoints/checkpoint_N

Idempotent (INSERT OR REPLACE sur l'id du programme) : réimporter un
checkpoint plus récent du même run met les lignées à jour.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
from oadlab import db  # noqa: E402


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    ckpt = Path(sys.argv[1]).resolve()
    programs_dir = ckpt / "programs"
    if not programs_dir.is_dir():
        sys.exit(f"pas de dossier programs/ dans {ckpt}")
    # runs/evolution/<run>/checkpoints/checkpoint_N -> <run>
    run = ckpt.parent.parent.name

    con = db.connect()
    count = 0
    for f in sorted(programs_dir.glob("*.json")):
        p = json.loads(f.read_text())
        meta = p.get("metadata") or {}
        db.upsert_program(
            con, id=p["id"], run=run, parent_id=p.get("parent_id"),
            generation=p.get("generation"),
            iteration=p.get("iteration_found"), ts=p.get("timestamp"),
            metrics=p.get("metrics"),
            changes=meta.get("changes") if isinstance(meta, dict) else None,
            code=p.get("code"))
        count += 1
    con.commit()
    con.close()
    print(f"{count} programmes importés dans la table programs (run {run})")


if __name__ == "__main__":
    main()
