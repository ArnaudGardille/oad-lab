"""Persistance SQLite des matchs."""

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
"""


def connect():
    config.RUNS.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def insert_match(con, *, candidate, opponent, opp_diff, cand_pos, spec,
                 cand_won, timed_out, game_s, turns, wall_s, replay_dir):
    con.execute(
        "INSERT INTO matches(ts, candidate, opponent, opp_diff, cand_pos,"
        " seed, aiseed, map, biome, civ, cand_won, timed_out, game_s,"
        " turns, wall_s, replay)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (time.time(), candidate, opponent, opp_diff, cand_pos,
         spec.seed, spec.aiseed, spec.map, spec.biome, spec.civ,
         cand_won, int(timed_out), game_s, turns, wall_s, replay_dir))
    con.commit()


def matches_for(con, candidate):
    return con.execute(
        "SELECT * FROM matches WHERE candidate = ? ORDER BY ts",
        (candidate,)).fetchall()
