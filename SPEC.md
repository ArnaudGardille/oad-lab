# Spécification — l'atelier d'exploration humain-LLM

Objet : l'outil collaboratif de création d'IA pour jeux de stratégie.
La boucle d'évolution est le moteur ; l'atelier est le produit. Cette
spec fixe les invariants de l'infrastructure (§1), le modèle de
données (§2), le front (§3) et l'ordre de construction (§4). Le
ROADMAP.md dit *quand* ; ce document dit *quoi* et *pourquoi*.

## 1. Propriétés nécessaires (invariants, non négociables)

**P1 — Une seule source de vérité : la base.** Tout fait (programme,
match, descripteur, événement de run, intention) atterrit dans
`runs/oadlab.sqlite`. Le front ne parse jamais un log ni un checkpoint
OpenEvolve : si une info l'intéresse, on l'importe en base
(`scripts/import_run.py`, `scripts/export_lineage.py`, idempotents).

**P2 — Les faits sont immuables, les interprétations recalculables.**
On n'update jamais un match ni un programme ; scores, ratings et
cartes se recalculent depuis les faits bruts. Chaque run porte sa
provenance : commit git, arbre sale ou non, digest de la config,
présence du hall of fame.

**P3 — La comparabilité est explicite.** Chaque match porte un
`protocol` (empreinte du pool d'adversaires + seeds + carte + civ +
timeout + nb de paires). Deux scores de protocoles différents ne se
comparent pas — le front refuse de les mettre sur un même axe. Un
score sans intervalle de confiance n'existe pas : l'incertitude est
une donnée de premier rang, un candidat estimé sur 24 parties et un
champion confirmé sur 200 ne doivent jamais se ressembler à l'écran.

**P4 — Boucle et front découplés par la base, dans les deux sens.**
La boucle écrit des faits, le front les lit (SQLite en WAL : lectures
live pendant les écritures). Le front n'agit sur la boucle que par la
table `intents`, que la boucle consomme à ses points sûrs. Tuer le
front n'affecte jamais un run ; un run planté laisse une base saine.

**P5 — Tout agent est désarmé par défaut.** Le générateur LLM produit
du texte, point (`evolution/bin/claude`, leçon de la nuit 1 : sans
cela il a édité le repo). Toute capacité d'action d'un agent futur
(digest, propositions) est une allowlist explicite ; `git status` du
repo vérifié en fin de run.

**P6 — Le budget est comptabilisé.** La monnaie du projet : parties
jouées et dollars LLM. Chaque run les enregistre ; le front les
affiche. Sans cela, impossible d'arbitrer explorer vs confirmer.

## 2. Modèle de données

Tables existantes : `matches` (une partie jouée : adversaire, position,
seeds, résultat, descripteurs comportementaux, protocole, replay),
`programs` (le DAG : parent, itération, métriques, résumé LLM du
changement, code).

Ajouts de cette spec :

- `runs` — un lancement (évolution ou campagne d'éval) : horodatages,
  provenance (commit, dirty, digest config), protocole, compteurs
  (itérations, parties, erreurs), état.
- `events` — le film d'un run, horodaté : itération terminée,
  programme évalué, nouveau meilleur, cellule MAP-Elites occupée,
  checkpoint, erreur, fin. C'est ce qui alimente le curseur temporel
  et le digest « depuis ta dernière visite ».
- `intents` — les verbes de pilotage, en attente ou consommés :
  `pin` / `cut` / `branch` (avec note d'intention textuelle) /
  `explore` (cellule comportementale). Auteur humain ou agent, statut,
  qui l'a consommée et quand. C'est un journal : une intention
  consommée reste en base.

## 3. Le front (l'atelier)

Une page, local uniquement, deux vues liées :

- **Carte comportementale** : grille aggression × boom, cellules
  colorées par meilleur score, occupation. La vue « où a-t-on
  cherché, où est le vide ».
- **DAG des lignées** : nœuds = programmes (taille/couleur = score et
  confiance), arêtes = parenté. Sélection liée à la carte.
- **Fiche d'un nœud** : métriques ± IC, diff de code vs parent, résumé
  LLM du changement, replays.
- **Curseur temporel** : rejouer le run (la carte se remplit itération
  par itération) — les events sont horodatés, c'est quasi gratuit.
- **Verbes** : épingler, couper, brancher-avec-intention, « explore
  ici » — le front écrit des intentions, il n'exécute jamais rien.
- **Nœuds fantômes + digest** : propositions d'agent en attente
  d'approbation ; au retour, le graphe diffé depuis la dernière visite.

Technique : petit serveur Python en lecture (endpoints JSON sur la
SQLite), polling 2-3 s (un run produit ~1 événement/min, le websocket
est un luxe), une page d3 sans chaîne de build. La sophistication va
dans le modèle de données, pas dans le framework.

**Le contenu doit être sémantique, pas du bruit** (décisions
2026-09-03) :

- **Hypothèse → prédiction → verdict.** Chaque mutation est une
  expérience : le générateur écrit en tête du programme une hypothèse
  stratégique et une prédiction directionnelle sur les descripteurs
  (`// HYPOTHESIS:` / `// PREDICTION:` — le contrat voyage DANS le
  code, donc survit à l'outillage). Après éval, le verdict est calculé
  mécaniquement (delta mesuré vs prédit, seuil d'effet 0,05) et stocké
  sur le nœud. Le DAG est un cahier de laboratoire : des hypothèses
  testées, pas des diffs. C'est aussi l'auto-instrumentation minimale :
  pour être pris au sérieux, l'algorithme doit NOMMER les indicateurs
  qu'il prétend faire bouger.
- **Vue « l'essentiel »** : par défaut de lecture, le DAG filtré aux
  itérations qui ont porté fruit (meilleurs successifs, prédictions
  confirmées, premiers occupants de cellule) et à leurs ancêtres.
- **Au clic sur un match : les courbes de la partie** (population,
  valeur détruite/perdue, contrôle de carte…), lues des séries du
  replay. Le lien programme↔matchs se fait par hash du code
  (`programs.code_sha` = tag `cand-<sha>` des matchs).
- **Clustering des politiques : par le comportement, pas par un LLM.**
  Les descripteurs + les cellules MAP-Elites clusterisent déjà ; un
  LLM sert à ÉTIQUETER un cluster (nommer un style depuis les
  hypothèses de ses membres), pas à le former.
- **L'interface est une API + un agent, le dashboard n'est qu'une
  vue.** Tout ce que la page montre passe par l'API JSON — donc tout
  agent peut le lire. Le panneau « analyste » incarne ça : un agent
  jetable et désarmé (P5 : texte seulement) qui reçoit le contexte
  (run, nœud sélectionné, matchs) et répond ; il suggère des verbes,
  l'humain les clique. L'adaptation de l'interface par l'agent
  (vues générées à la demande) est l'étape d'après, pas un dû.

Dette assumée (généricité) : les colonnes `aggression`/`boom`/… sont
nommées en dur alors qu'elles sont spécifiques à 0 A.D. La découpe
« laboratoire générique / adaptateur de jeu » (jouer, descripteurs,
substrat de politique, ancres) ne sera faite qu'au portage vers un
deuxième jeu — on ne généralise bien qu'au deuxième exemplaire. D'ici
là : toute connaissance de 0 A.D. reste confinée à `game.py`,
`descriptors.py`, `config.py` et au mod.

## 4. Ordre de construction

1. **Le spinal** (cette étape) : tables `runs`/`events`/`intents`,
   protocole sur chaque match, manifeste de provenance écrit par
   `run_night.sh`, import idempotent des runs passés.
2. **MVP du front** : carte + DAG + fiche nœud, lecture seule, conçu
   contre les données réelles de la nuit 1.
3. **Les verbes** : la table `intents` branchée comme priors de
   sélection dans la boucle — une fois qu'on *voit*, on saura lesquels
   manquent vraiment.
4. Ensuite seulement : ouverture de l'espace de recherche (modules de
   comportement, pas seulement config.js) et confirmation systématique
   des champions (~200 parties) avant hall of fame.
