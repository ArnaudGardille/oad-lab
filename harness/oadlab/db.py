"""Persistance SQLite : matchs (+ descripteurs) et DAG des lignées."""

import sqlite3
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches(
    id INTEGER PRIMARY KEY,
    ts REAL,
    candidate TEXT NOT NULL,
    opponent TEXT NOT NULL,
    opp_diff INTEGER,
    cand_pos INTEGER,          -- 1 ou 2
    seed INTEGER, aiseed INTEGER,
    map TEXT, biome TEXT, civ TEXT,
    cand_won INTEGER,          -- 1/0, NULL si sans résultat (timeout)
    timed_out INTEGER,
    game_s REAL, turns INTEGER, wall_s REAL,
    replay TEXT
);
CREATE INDEX IF NOT EXISTS idx_matches_candidate ON matches(candidate);

-- DAG des lignées, importé des checkpoints OpenEvolve
-- (scripts/export_lineage.py). Même base que les matchs : c'est elle
-- que l'atelier visuel (phase 6) lira.
CREATE TABLE IF NOT EXISTS programs(
    id TEXT PRIMARY KEY,
    run TEXT,                  -- nom du run (runs/evolution/<run>/)
    parent_id TEXT,
    generation INTEGER,
    iteration INTEGER,
    ts REAL,
    combined_score REAL,
    wr_easy REAL, wr_medium REAL, wr_hard REAL,
    aggression REAL, boom REAL,
    changes TEXT,              -- résumé LLM des modifications
    code TEXT
);
"""

# Descripteurs comportementaux du candidat, une valeur par match
# (moyennés par batch dans evalapi). Ajoutés par ALTER pour ne pas
# invalider les bases existantes.
DESCRIPTOR_COLS = ("aggression", "boom", "military", "map_control")


def connect():
    config.RUNS.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for col in DESCRIPTOR_COLS:
        try:
            con.execute(f"ALTER TABLE matches ADD COLUMN {col} REAL")
        except sqlite3.OperationalError as e:
            # Seule la colonne déjà présente est bénigne ; un verrou
            # ("database is locked") avalé ici ferait planter le
            # prochain INSERT avec un "no such column" mystérieux.
            if "duplicate column" not in str(e):
                raise
    return con


def insert_match(con, *, candidate, opponent, opp_diff, cand_pos, spec,
                 cand_won, timed_out, game_s, turns, wall_s, replay_dir,
                 descriptors=None):
    desc = descriptors or {}
    con.execute(
        "INSERT INTO matches(ts, candidate, opponent, opp_diff, cand_pos,"
        " seed, aiseed, map, biome, civ, cand_won, timed_out, game_s,"
        " turns, wall_s, replay, aggression, boom, military, map_control)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (time.time(), candidate, opponent, opp_diff, cand_pos,
         spec.seed, spec.aiseed, spec.map, spec.biome, spec.civ,
         cand_won, int(timed_out), game_s, turns, wall_s, replay_dir,
         desc.get("aggression"), desc.get("boom"), desc.get("military"),
         desc.get("map_control")))
    con.commit()


def upsert_program(con, *, id, run, parent_id, generation, iteration, ts,
                   metrics, changes, code):
    m = metrics or {}
    con.execute(
        "INSERT OR REPLACE INTO programs(id, run, parent_id, generation,"
        " iteration, ts, combined_score, wr_easy, wr_medium, wr_hard,"
        " aggression, boom, changes, code)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (id, run, parent_id, generation, iteration, ts,
         m.get("combined_score"), m.get("wr_easy"), m.get("wr_medium"),
         m.get("wr_hard"), m.get("aggression"), m.get("boom"),
         changes, code))
    con.commit()


def matches_for(con, candidate):
    return con.execute(
        "SELECT * FROM matches WHERE candidate = ? ORDER BY ts",
        (candidate,)).fetchall()
