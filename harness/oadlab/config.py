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

# Seeds de CONFIRMATION, disjoints des précédents : le stage 3 rejuge
# les élites sur des parties qu'aucune sélection n'a vues. Sans cela,
# le score qui promeut un programme est le même tirage que celui qui
# l'a fait gagner — la malédiction du vainqueur (mesurée le
# 2026-09-04 : sous l'hypothèse « tous les programmes valent le
# baseline », le max attendu de 60 tirages à 24 parties vaut 0,77,
# au-dessus du meilleur score jamais observé).
CONFIRM_SEEDS = list(range(201, 217))   # 16 seeds x 2 positions x
                                        # pool : 96 parties sur les 3
                                        # ancres, 192 sur un pool de 6

# Ancres : Petra vanilla à trois difficultés (2 facile, 3 moyen, 4 dur).
ANCHORS = [("petra", 2), ("petra", 3), ("petra", 4)]
CANDIDATE_DIFF = 3

# Personnalité des bots du mod (candidate, forkbot, hofN) — épinglée
# dans _petrabot.js, PAS via la ligne de commande : 0 A.D. 0.28 n'a pas
# d'option --autostart-aibehavior (seuls -ai, -aidiff, -aiseed
# existent), et le défaut de Petra est `behavior || "random"`, soit
# personality.aggressive tiré uniformément sur [0, 1] à chaque partie.
# Conséquence mesurée le 2026-09-04 : les branches discontinues du type
# `personality.aggressive > personalityCut.strong` (0,7) ne
# s'exécutaient que dans ~30 % des parties, choisies au hasard — une
# mutation portée par une de ces branches voyait son effet dilué d'un
# facteur 3 et le « bot » était en fait un mélange aléatoire de
# personas. "balanced" borne le tirage à [0,37 ; 0,63] : la politique
# redevient une fonction du seul programme évolué.
# Entre dans le protocole (P3) : les données d'avant et d'après ne se
# comparent pas.
AI_BEHAVIOR = "balanced"

# Manifeste du hall of fame (scripts/make_hof.py). S'il existe, les
# bots listés rejoignent le pool d'évaluation aux côtés des ancres —
# le combined_score change alors d'échelle, c'est voulu (cible mobile,
# façon league play).
HOF_MANIFEST = RUNS / "hof.json"

PARALLEL = 12          # 1 partie par cœur physique (Ryzen 3900X)
GAME_TIMEOUT = 240     # cap mural par partie = cap de tours de facto
STAGGER = 2.0          # évite les collisions de nommage des replays
