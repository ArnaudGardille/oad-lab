# oad-lab — orientation

Ce fichier dit *ce qu'on cherche* et *ce qui casse facilement*. Il ne
décrit ni les fichiers ni les commandes : ça change toutes les
semaines, et un document faux est pire qu'un document absent.

## L'objet

Recherche automatisée de politiques de jeu pour 0 A.D. : un agent LLM
édite des bots JS natifs, un harnais les évalue en parallèle, une
archive qualité-diversité garde les stratégies distinctes.

**La boucle d'évolution est le moteur, l'atelier est le produit.** On
veut les deux à la fois : du winrate mesuré proprement, et des
stratégies interprétables. Les deux se tiennent — un gain qu'on sait
expliquer se transfère, se combine et se corrige, là où un gain opaque
ne se rejoue nulle part — et l'atelier existe pour rendre cette
tension visible plutôt que de la trancher une fois pour toutes : ce
qui gagne doit pouvoir se raconter (hypothèse → prédiction → verdict),
ce qui se raconte bien doit se vérifier au score.

L'ensemble sert une recherche que ni l'humain ni le LLM ne mènerait
seul : voir ce que la boucle explore, comprendre pourquoi une stratégie
gagne, et infléchir la suite.

## Ce qui n'est pas négociable

Les six invariants sont dans `SPEC.md` (§1) et n'ont pas à être
recopiés ici. Ce qu'il faut en retenir avant d'écrire une ligne :

- **La base est la seule source de vérité.** Un fait qui n'est pas en
  base n'existe pas ; on n'ajoute jamais un parseur de log dans le
  front, on importe le fait.
- **Un score sans protocole ni intervalle de confiance n'existe pas.**
  Changer le pool d'adversaires, les seeds, la carte, le timeout ou la
  version du moteur change l'échelle : les scores d'avant et d'après ne
  se comparent pas, et l'outil doit refuser de les mettre sur un même
  axe plutôt que de laisser croire à un progrès.
- **On n'update jamais un fait.** Les scores, ratings et cartes se
  recalculent depuis les matchs bruts.
- **Tout agent est désarmé par défaut.** Le générateur produit du
  texte ; toute capacité d'action est une allowlist explicite.

## Ce que le jeu impose (appris à la dure)

- **L'évaluation est statistique, jamais reproductible au tour près.**
  L'IA calcule dans un thread asynchrone : à seed, carte et civ figées,
  deux parties divergent. Toute affirmation tirée d'une seule partie
  est du bruit.
- **Un replay ne rejoue pas l'IA qui a joué.** `commands.txt` ne
  contient aucune commande : le moteur ré-exécute le bot installé sous
  ce nom au moment du replay. Un replay est une reconstitution, pas une
  preuve — et pour un bot dont la config a changé depuis, c'est une
  reconstitution trompeuse.
- **Une partie sans résultat compte comme une défaite.** Sinon un bot
  qui fige la partie ou fait planter le moteur est récompensé.
- **Le snap se défend.** Sous un label AppArmor de session, les
  `kill()` vers les processus du jeu partent en EPERM et les parties en
  timeout fuient à plusieurs Go pièce jusqu'à saturer la RAM. Tout ce
  qui doit survivre à un shell ou tuer une partie passe par
  `systemd --user`.
- **Le budget est la monnaie.** Une nuit, c'est un nombre de parties et
  un montant de LLM. Une idée qui coûte 300 parties doit se justifier
  contre celle qui en coûte 30.

## Où écrire quoi

- `SPEC.md` — invariants, modèle de données, le *pourquoi* de
  l'infrastructure.
- `ROADMAP.md` — les phases, leur état, leurs critères de sortie
  mesurables : le *quand*.
- `README.md` — l'environnement épinglé et les points d'entrée : de
  quoi relancer la machine demain.
- ici — l'objet et les pièges durables.
- le code — le reste, en commentaires qui disent *pourquoi*, au plus
  près de la ligne concernée.

Une décision qui n'entre dans aucune de ces cases n'a probablement pas
besoin d'être écrite.
