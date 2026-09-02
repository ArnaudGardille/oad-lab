# oad-lab

Recherche automatisée de politiques de jeu pour 0 A.D. : une boucle
autoresearch (un agent LLM édite des bots JS natifs), un harnais
d'évaluation parallèle, une archive qualité-diversité des stratégies,
et à terme un atelier visuel d'exploration humain-LLM.

Voir [ROADMAP.md](ROADMAP.md) pour le plan et l'état d'avancement.

## Environnement épinglé

- **0 A.D. 0.28.0**, installé via snap (`/snap/bin/0ad`, révision 741).
  L'API des mods change entre alphas : toute montée de version se note ici
  et se re-valide contre le smoke test de la phase 1.
- Dossier mods du snap : `~/snap/0ad/current/.local/share/0ad/mods`
  (les bots de `bots/` y sont liés/copiés par le harnais).
- Données utilisateur du jeu (replays inclus) :
  `~/snap/0ad/current/.local/share/0ad/`.
- Contrainte snap : l'interface `removable-media` n'est pas connectée,
  le jeu ne peut donc pas écrire sur le disque data. Design retenu :
  le jeu écrit dans son dossier snap, le harnais moissonne les replays
  vers `runs/matches/` (gitignoré, disque système) — le disque data
  (UUID `8d486440-…`) est actuellement débranché ; quand il reviendra,
  `runs/` pourra migrer vers
  `/media/maitre/8d486440-…/Documents/oad-runs` (toujours identifié
  par UUID, jamais par /dev/sdX).

## Structure

- `harness/` — évaluation : runner parallèle, Elo, SQLite, CLI (Python, uv)
- `bots/` — un dossier de mod par bot (JS natif, format mod 0 A.D.)
- `analysis/` — extraction de descripteurs comportementaux depuis les replays
