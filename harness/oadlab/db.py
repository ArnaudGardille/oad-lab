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

-- Un lancement (nuit d'évolution, campagne d'éval) avec sa
-- provenance. Importé par scripts/import_run.py (SPEC.md §2).
CREATE TABLE IF NOT EXISTS runs(
    id TEXT PRIMARY KEY,       -- ex. "2026-09-02_1948"
    kind TEXT,                 -- "evolution" | "eval"
    started REAL, finished REAL,
    git_commit TEXT, dirty INTEGER,
    config_sha TEXT,           -- sha1 de evolution/config.yaml
    hof INTEGER,               -- le hall of fame était-il dans le pool
    iterations INTEGER,        -- demandées
    iterations_done INTEGER,
    errors INTEGER,            -- itérations perdues (diffs invalides...)
    games INTEGER,             -- parties jouées (budget, P6)
    status TEXT                -- "running" | "completed" | "crashed"
);

-- Le film d'un run : alimente le curseur temporel et le digest.
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY,
    run TEXT NOT NULL,
    ts REAL,
    kind TEXT,                 -- iteration | evaluated | new_best |
                               -- cell_occupied | checkpoint | error |
                               -- completed
    iteration INTEGER,
    program_id TEXT,
    payload TEXT               -- JSON (métriques, message...)
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run, ts);

-- Les verbes de pilotage (SPEC.md §3) : le front les écrit, la boucle
-- les consomme à ses points sûrs. Journal — jamais supprimés.
CREATE TABLE IF NOT EXISTS intents(
    id INTEGER PRIMARY KEY,
    ts REAL,
    author TEXT,               -- "user" | "agent"
    verb TEXT,                 -- pin | cut | branch | explore
    target TEXT,               -- program_id ou cellule "agg,boom"
    note TEXT,                 -- intention textuelle (verbe branch)
    status TEXT DEFAULT 'pending',  -- pending | consumed | dismissed
    consumed_ts REAL,
    consumed_by TEXT           -- run qui l'a consommée
);

-- Le carnet de laboratoire (SPEC.md §3 bis, chantier A) : ce que les
-- expériences ont appris, distillé par un agent, injecté dans le
-- prompt du générateur par le composeur.
CREATE TABLE IF NOT EXISTS lessons(
    id INTEGER PRIMARY KEY,
    ts REAL,                   -- dernière mise à jour
    kind TEXT NOT NULL,        -- law | impasse | question
    statement TEXT NOT NULL,   -- une phrase, actionnable
    confidence TEXT,           -- ex. "confirmé 3x", "faible", "contesté"
    evidence TEXT,             -- JSON : ids de programmes-preuves
    status TEXT DEFAULT 'active',  -- active | retired
    updated_by TEXT            -- cycle/run qui l'a touchée
);

-- Budget des agents cognitifs (P6) : chaque appel LLM d'un rôle
-- (distiller, reporter, pi, analyst) est compté comme les parties.
CREATE TABLE IF NOT EXISTS agent_runs(
    id INTEGER PRIMARY KEY,
    ts REAL,
    role TEXT NOT NULL,
    run TEXT,                  -- cycle concerné, si applicable
    cost_usd REAL,
    ok INTEGER,                -- 1 réussi, 0 échec
    detail TEXT
);

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

# Préfixe du champ `changes` d'un clone de migration inter-îlots
# (posé par export_lineage, filtré par distill) : OpenEvolve copie le
# programme tel quel — même code, mêmes métriques — vers un îlot
# voisin ; ce nœud n'est pas une expérience et ne doit jamais être
# présenté au distillateur comme une mutation à effet nul.
MIGRATION_PREFIX = "[migration]"

# Colonnes valides de la table `runs` (hors `id`, géré à part) —
# allowlist pour upsert_run : ses **fields alimentent un f-string SQL,
# import_run.py (qui parse log + manifeste externes) est le genre
# d'appelant qui accrète des clés au fil du temps.
RUN_COLS = frozenset({
    "kind", "started", "finished", "git_commit", "dirty", "config_sha",
    "hof", "iterations", "iterations_done", "errors", "games", "status",
})

# Migrations douces : colonnes ajoutées après coup aux tables.
_MIGRATIONS = {
    "matches": [(col, "REAL") for col in DESCRIPTOR_COLS] + [
        ("protocol", "TEXT"),  # empreinte du protocole d'éval (P3)
    ],
    "programs": [
        ("games", "REAL"),     # nb de parties de l'éval → confiance (P3)
        ("code_sha", "TEXT"),  # sha1[:12] du code = lien vers matches
                               # (candidate = "cand-<sha>")
        # Score de CONFIRMATION (stage 3) : rejoué sur des seeds
        # disjoints de ceux qui ont sélectionné le programme, donc sous
        # un autre protocole que `combined_score` — colonnes séparées,
        # jamais le même axe (P3).
        ("confirmed_score", "REAL"),
        ("confirmed_games", "REAL"),
        ("hypothesis", "TEXT"),   # l'hypothèse stratégique déclarée
        ("prediction", "TEXT"),   # l'effet prédit sur les descripteurs
        ("verdict", "TEXT"),      # confrontation prédiction/mesure
    ],
}


# Schéma + migrations : une seule fois par processus. Les refaire à
# chaque connect() prendrait un verrou d'écriture (DDL) à chaque
# requête du serveur de l'atelier — contention inutile avec la boucle.
_schema_ready = False


def connect():
    global _schema_ready
    config.RUNS.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    # WAL : le front lit en continu pendant que la boucle écrit (P4).
    # busy_timeout : WAL ne couvre pas écrivain-vs-écrivain — sans lui,
    # un import pendant une nuit d'évolution planterait en
    # "database is locked" au lieu d'attendre.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    if _schema_ready:
        return con
    con.executescript(SCHEMA)
    for table, cols in _MIGRATIONS.items():
        for col, typ in cols:
            try:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError as e:
                # Seule la colonne déjà présente est bénigne ; un
                # verrou ("database is locked") avalé ici ferait
                # planter le prochain INSERT avec un "no such column".
                if "duplicate column" not in str(e):
                    raise
    _schema_ready = True
    return con


def insert_match(con, *, candidate, opponent, opp_diff, cand_pos, spec,
                 cand_won, timed_out, game_s, turns, wall_s, replay_dir,
                 descriptors=None, protocol=None):
    desc = descriptors or {}
    con.execute(
        "INSERT INTO matches(ts, candidate, opponent, opp_diff, cand_pos,"
        " seed, aiseed, map, biome, civ, cand_won, timed_out, game_s,"
        " turns, wall_s, replay, aggression, boom, military, map_control,"
        " protocol)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (time.time(), candidate, opponent, opp_diff, cand_pos,
         spec.seed, spec.aiseed, spec.map, spec.biome, spec.civ,
         cand_won, int(timed_out), game_s, turns, wall_s, replay_dir,
         desc.get("aggression"), desc.get("boom"), desc.get("military"),
         desc.get("map_control"), protocol))
    con.commit()


def upsert_run(con, *, id, **fields):
    bad = set(fields) - RUN_COLS
    if bad:
        raise ValueError(f"colonnes runs inconnues: {sorted(bad)}")
    cols = ["id"] + list(fields)
    con.execute(
        f"INSERT OR REPLACE INTO runs({','.join(cols)})"
        f" VALUES({','.join('?' * len(cols))})",
        [id] + list(fields.values()))
    con.commit()


def replace_events(con, run, events):
    """Remplace le film d'un run (import idempotent).

    ATTENTION : les `id` des events sont réattribués à chaque
    réimport — un run en cours est réimporté en continu. Le front doit
    donc cursorer sur (run, ts), jamais sur id."""
    con.execute("DELETE FROM events WHERE run = ?", (run,))
    con.executemany(
        "INSERT INTO events(run, ts, kind, iteration, program_id, payload)"
        " VALUES(?,?,?,?,?,?)",
        [(run, e.get("ts"), e.get("kind"), e.get("iteration"),
          e.get("program_id"), e.get("payload")) for e in events])
    con.commit()


def add_intent(con, *, author, verb, target, note=None):
    con.execute(
        "INSERT INTO intents(ts, author, verb, target, note)"
        " VALUES(?,?,?,?,?)", (time.time(), author, verb, target, note))
    con.commit()


def pending_intents(con):
    return con.execute(
        "SELECT * FROM intents WHERE status = 'pending' ORDER BY ts"
    ).fetchall()


def consume_intent(con, intent_id, consumed_by):
    con.execute(
        "UPDATE intents SET status = 'consumed', consumed_ts = ?,"
        " consumed_by = ? WHERE id = ? AND status = 'pending'",
        (time.time(), consumed_by, intent_id))
    con.commit()


def upsert_program(con, *, id, run, parent_id, generation, iteration, ts,
                   metrics, changes, code, code_sha=None, hypothesis=None,
                   prediction=None, verdict=None):
    m = metrics or {}
    # UPSERT plutôt qu'INSERT OR REPLACE : le verdict d'un programme
    # dont le parent a été élagué des checkpoints suivants deviendrait
    # None au réimport — un verdict acquis ne doit jamais régresser.
    con.execute(
        "INSERT INTO programs(id, run, parent_id, generation,"
        " iteration, ts, combined_score, wr_easy, wr_medium, wr_hard,"
        " aggression, boom, games, confirmed_score, confirmed_games,"
        " changes, code, code_sha, hypothesis, prediction, verdict)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(id) DO UPDATE SET"
        " run=excluded.run, parent_id=excluded.parent_id,"
        " generation=excluded.generation, iteration=excluded.iteration,"
        " ts=excluded.ts, combined_score=excluded.combined_score,"
        " wr_easy=excluded.wr_easy, wr_medium=excluded.wr_medium,"
        " wr_hard=excluded.wr_hard, aggression=excluded.aggression,"
        " boom=excluded.boom, games=excluded.games,"
        # Un score confirmé coûte 96 à 192 parties : il ne régresse
        # jamais à NULL parce qu'un checkpoint plus récent a perdu la
        # métrique.
        " confirmed_score=COALESCE(excluded.confirmed_score,"
        " programs.confirmed_score),"
        " confirmed_games=COALESCE(excluded.confirmed_games,"
        " programs.confirmed_games),"
        " changes=excluded.changes, code=excluded.code,"
        " code_sha=excluded.code_sha, hypothesis=excluded.hypothesis,"
        " prediction=excluded.prediction,"
        " verdict=COALESCE(excluded.verdict, programs.verdict)",
        (id, run, parent_id, generation, iteration, ts,
         m.get("combined_score"), m.get("wr_easy"), m.get("wr_medium"),
         m.get("wr_hard"), m.get("aggression"), m.get("boom"),
         m.get("games"), m.get("confirmed_score"),
         m.get("confirmed_games"), changes, code, code_sha, hypothesis,
         prediction, verdict))
    con.commit()


def active_lessons(con):
    return con.execute(
        "SELECT * FROM lessons WHERE status = 'active'"
        " ORDER BY kind, id").fetchall()


LESSON_KINDS = ("law", "impasse", "question")


def apply_lesson_ops(con, ops, updated_by, max_active=None):
    """Applique les opérations du distillateur : add / update / retire.
    Rend (appliquées, rejetées). Les opérations malformées sont
    rejetées une à une — jamais tout le lot. `max_active` est le
    plafond STRUCTUREL du carnet : la consigne de fusion du prompt ne
    suffit pas, un carnet qui enfle sans borne est le symptôme d'un
    distillateur qui empile au lieu d'apprendre."""
    import json as _json
    now = time.time()
    done, bad = 0, []
    active = con.execute(
        "SELECT count(*) FROM lessons WHERE status='active'"
    ).fetchone()[0]
    for op in ops:
        try:
            action = op["op"]
            if action == "add":
                if op.get("kind") not in LESSON_KINDS:
                    raise ValueError(f"kind invalide: {op.get('kind')!r}")
                if not op.get("statement"):
                    raise ValueError("statement vide")
                if max_active is not None and active >= max_active:
                    raise ValueError(
                        f"carnet plein ({max_active}) — fusionner ou"
                        " retirer avant d'ajouter")
                con.execute(
                    "INSERT INTO lessons(ts, kind, statement, confidence,"
                    " evidence, updated_by) VALUES(?,?,?,?,?,?)",
                    (now, op["kind"], op["statement"],
                     op.get("confidence"),
                     _json.dumps(op.get("evidence", [])), updated_by))
                active += 1
            elif action == "update":
                cur = con.execute(
                    "UPDATE lessons SET ts=?, statement=?, confidence=?,"
                    " evidence=?, updated_by=? WHERE id=? AND"
                    " status='active'",
                    (now, op["statement"], op.get("confidence"),
                     _json.dumps(op.get("evidence", [])), updated_by,
                     int(op["id"])))
                if cur.rowcount == 0:
                    raise KeyError(f"lesson {op['id']} inconnue")
            elif action == "retire":
                cur = con.execute(
                    "UPDATE lessons SET ts=?, status='retired',"
                    " updated_by=? WHERE id=? AND status='active'",
                    (now, updated_by, int(op["id"])))
                if cur.rowcount == 0:
                    raise KeyError(f"lesson {op['id']} inconnue")
                active -= 1
            else:
                raise ValueError(f"op inconnue: {action}")
            done += 1
        except (KeyError, ValueError, TypeError, sqlite3.Error) as e:
            # sqlite3.Error inclus : une contrainte violée (NOT NULL...)
            # rejette CETTE op, pas tout le lot.
            bad.append(f"{op!r}: {e}")
    con.commit()
    return done, bad


def record_agent_run(con, *, role, run=None, cost_usd=None, ok=True,
                     detail=None):
    con.execute(
        "INSERT INTO agent_runs(ts, role, run, cost_usd, ok, detail)"
        " VALUES(?,?,?,?,?,?)",
        (time.time(), role, run, cost_usd, int(ok), detail))
    con.commit()


def matches_for(con, candidate):
    return con.execute(
        "SELECT * FROM matches WHERE candidate = ? ORDER BY ts",
        (candidate,)).fetchall()
