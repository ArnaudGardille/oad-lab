"""Configuration du harnais : chemins, jeu d'évaluation figé, budgets."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "runs"
DB_PATH = RUNS / "oadlab.sqlite"
MATCHES_DIR = RUNS / "matches"
SNAP_REPLAYS = Path.home() / "snap/0ad/current/.local/share/0ad/replays/0.28.0"

GAME_CMD = "0ad"
GAME_VERSION = "0.28.0"   # version épinglée (README) — entre dans le
                          # protocole d'éval : changer de moteur casse
                          # la comparabilité des scores (SPEC.md P3)
MODS = ["public", "oadlab"]

# Conditions de partie figées : tout ce qui n'est pas fixé ici est une
# source de variance non contrôlée (le biome est tiré au hasard par
# défaut, les civs aussi). Même civ des deux côtés = match symétrique.
MAP = "random/mainland"
MAP_SIZE = 128
BIOME = "generic/temperate"
CIV = "spart"

# Jeu de seeds d'évaluation figé, distinct des seeds d'exploration que
# la boucle autoresearch pourra utiliser librement.
EVAL_SEEDS = [101, 102, 103, 104, 105, 106]

# Ancres : Petra vanilla à trois difficultés (2 facile, 3 moyen, 4 dur).
ANCHORS = [("petra", 2), ("petra", 3), ("petra", 4)]
CANDIDATE_DIFF = 3

# Manifeste du hall of fame (scripts/make_hof.py). S'il existe, les
# bots listés rejoignent le pool d'évaluation aux côtés des ancres —
# le combined_score change alors d'échelle, c'est voulu (cible mobile,
# façon league play).
HOF_MANIFEST = RUNS / "hof.json"

PARALLEL = 12          # 1 partie par cœur physique (Ryzen 3900X)
GAME_TIMEOUT = 240     # cap mural par partie = cap de tours de facto
STAGGER = 2.0          # évite les collisions de nommage des replays
