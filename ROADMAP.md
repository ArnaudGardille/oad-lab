# Roadmap

Chaque phase a un livrable et un critère de sortie mesurable. On ne passe
pas à la suivante tant que le critère n'est pas tenu — en particulier,
tout le dimensionnement du projet dépend du chiffre de la phase 1.

## Phase 0 — Fondations — FAIT (2026-09-02)

- [x] Repo, structure, version 0 A.D. épinglée (0.28.0 snap)
- [x] Localiser mods dir et données snap
- [x] Vérifier que `0ad` accepte les flags CLI à travers le wrapper snap —
      oui, validé ; pas besoin de réinstaller hors snap
- [ ] Créer `oad-runs/` sur le disque data — **BLOQUÉ 2026-09-02 : le
      disque data (UUID 8d486440-…) est absent du système** (ni monté,
      ni visible dans lsblk ; le seul ext4 de ~1 To présent est un
      disque Steam, UUID 18ee8766-…). En attendant, les replays restent
      dans `runs/` sur le disque système (gitignoré) — acceptable à
      l'échelle des phases 1-4 (~200 Ko/partie), à revoir avant toute
      campagne massive.

**Sortie** : commande headless validée :

```
0ad --autostart="random/mainland" --autostart-size=128 \
    --autostart-ai=1:petra --autostart-ai=2:petra --autostart-nonvisual
```

## Phase 1 — Mesure — FAIT (2026-09-02)

- [x] Partie Petra vs Petra headless : se termine naturellement par
      conquête (metadata.json : `playerStates[].state` = won/defeated,
      `timeElapsed` en ms)
- [x] 12 parties en parallèle (1/cœur physique, Ryzen 3900X, seeds
      1-12, `harness/phase1.py`) : **12/12 finies et décisives**, mur
      médian 28 s (min 16, max 45), durées de jeu 16-42 min, accélération
      27-84× sous charge, vainqueurs équilibrés 6/6 entre positions.
      **Débit mesuré : ~1 500 parties/h à parallélisme 12.**
- [x] Pas de collision de dossiers replay avec démarrages décalés de
      3 s ; appariement seed↔replay via commands.txt fiable.
- [ ] Cap de tours + timeout mural à intégrer au runner définitif
      (phase 3) — phase1.py a déjà le timeout mural + kill par groupe.

Pièges documentés : `--writableRoot` fige le jeu au démarrage (ne pas
utiliser) ; le wrapper snap laisse un pyrogenesis orphelin si on ne tue
pas le groupe de processus (`start_new_session` + `killpg`).

**Sortie** : le débit d'évaluation (parties/heure) est connu et noté ici.
C'est le chiffre qui dimensionne cascade, tailles de cartes et budget
par candidat.

## Phase 2 — Pipeline bot custom — FAIT (2026-09-02)

- [x] Mod `bots/oadlab/` (symlinké dans le dossier mods du snap) avec
      `nullbot` (ne fait rien, validé : Petra l'élimine par conquête)
      et `forkbot` (fork de Petra)
- [x] Fork trivial en 0.28 : Petra est en modules ES, un sed sur les
      chemins d'import suffit, pas de collision de namespace
- [x] Validation : **forkbot 12/24 (50,0%) contre Petra vanilla** sur
      12 seeds mirrorées, 24/24 parties exploitables, 163 s au total

Enseignements pour le runner (phase 3) :
- **Biais de position massif : le joueur 1 gagne 18/24 (75%)** dans ce
  run — les paires mirrorées ne sont pas un luxe, elles sont
  obligatoires pour toute mesure.
- Les parties ne sont PAS reproductibles d'un run à l'autre sans
  `--autostart-aiseed` (une partie infinie de 3,4 M de tours au premier
  run s'est finie en 20 min au second). Fixer seed ET aiseed pour la
  reproductibilité ; varier aiseed pour l'indépendance statistique.
- Timeout PAR PARTIE (240 s) = cap de tours de facto ; ~1 partie sur 12
  peut être interminable. Kill multi-étages requis (killpg parfois
  EPERM sous snap → kill direct + pkill -P).

**Sortie** : le pipeline édition → chargement → partie → mesure
fonctionne de bout en bout.

## Phase 3 — Harnais d'évaluation (l'actif durable) — FAIT (2026-09-02)

Package `harness/oadlab/` (venv uv `.venv`, dépendance : openskill).
Usage : `PYTHONPATH=harness .venv/bin/python -m oadlab eval <bot>`.

- [x] Runner parallèle : pool borné (12), timeout par partie (240 s),
      kill multi-étages, moisson des replays vers `runs/matches/`
- [x] Conditions figées : mainland 128, biome `generic/temperate`,
      civs identiques des deux côtés (spart/spart), seeds d'éval
      dédiées (101-106), aiseed déterministe par partie
- [x] Ancres : Petra vanilla à difficulté 2/3/4 via
      `--autostart-aidiff=JOUEUR:NIVEAU` (par joueur — pas besoin de
      forks d'ancrage)
- [x] SQLite `runs/oadlab.sqlite` (table matches), rapport winrate par
      ancre avec IC de Wilson 95% + rating openskill (ancres épinglées)
- [x] CLI : `eval <bot> [--pairs N]`, `report <bot>`, `selftest`
- [x] Éval de validation (24 parties, 98 s, 0 timeout) : forkbot
      **88% vs facile, 50% vs moyen, 25% vs dur** — l'ordre attendu,
      et la parité exacte du clone contre son original. Openskill :
      ordinal 14,7 (mu 25,8, sigma 3,71).

**Découverte importante (selftest)** : même avec seed + aiseed + biome
+ civs figés, deux runs de la même partie divergent — l'IA calcule
dans un thread asynchrone, ses commandes dépendent du timing CPU.
L'évaluation est donc statistique par nature : paires mirrorées et
nombre de seeds font la précision ; pas de reproduction exacte
partie-à-partie, pas de variance reduction par common random numbers.

**Sortie** : évaluer un bot donne winrates ± IC et un rating, sans
intervention, en un temps connu (~24 parties ≈ 4-6 min).

## Phase 4 — Boucle autoresearch linéaire

- [ ] Cascade d'évaluation : (a) le bot se charge et survit 2 min,
      (b) 3 parties courtes vs Petra easy, (c) éval complète phase 3
- [ ] Agent (claude -p / Agent SDK) : édite le bot candidat, lance la
      cascade, commit git si amélioration, note d'expérience par essai
- [ ] Première nuit de run — détachée de la session (setsid/nohup),
      logs persistants

**Sortie** : la boucle tourne 8 h sans intervention et produit un log
d'expériences exploitable au matin.

## Phase 5 — Archive qualité-diversité

- [ ] Descripteurs comportementaux extraits des replays (timing
      d'agression, ratio éco/militaire, harcèlement, timing de phase...)
- [ ] DAG des lignées en base (parent, diff, descripteurs, Elo, notes)
- [ ] Sélection de branche : potentiel + originalité (nouveauté mesurée
      sur le comportement, pas sur le code)
- [ ] Hall of fame divers comme pool d'évaluation (+ ancres Petra)

**Sortie** : la carte comportementale se remplit ; les runs ne
convergent plus vers un style unique.

## Phase 6 — Atelier visuel humain-LLM

- [ ] Serveur local lisant la même SQLite, push websocket
- [ ] Vue DAG des lignées + carte comportementale (d3/cytoscape)
- [ ] Verbes : épingler, couper, brancher-avec-intention, drapeau
      « explore ici » → traduits en priors de sélection
- [ ] Nœuds fantômes : propositions de l'agent, approbation en un clic,
      auto-approbation la nuit
- [ ] Digest du matin : le graphe diffé (pousses surlignées, branches
      mortes estompées, replays marquants)

**Sortie** : une session de pilotage complète (regarder, orienter,
relancer) sans écrire une ligne de code ni de prompt.

## Décisions d'outillage (prospection GitHub, 2026-09-02)

- **Moteur d'évolution (phases 4-5) : forker OpenEvolve**
  (`algorithmicsuperintelligence/openevolve`, ex-codelion, Apache-2.0,
  actif) plutôt qu'écrire une boucle custom. Il apporte d'office :
  archive MAP-Elites en îlots, cascade d'évaluation, checkpointing
  reprenable, et un backend « Claude Code CLI » documenté. Son contrat
  d'évaluateur (fonction Python → metrics + artifacts) épouse exactement
  notre harnais. À lui seul il couvre une grande partie de la phase 5.
- **À piller ensuite** : ShinkaEvolve (rejection-sampling de nouveauté,
  bandit sur ensemble de LLMs), AIDE (scoring UCB des nœuds pour la
  sélection de branche). autoresearch de Karpathy : référence de
  minimalisme seulement. AI-Scientist-v2 (licence restrictive) et
  FunSearch (squelette gelé) : écartés.
- **Harnais : le nôtre.** Aucun harnais de match 0 A.D. basé replays
  n'existe sur GitHub (tout passe par l'interface RL socket). Confirmé
  qu'on écrit le runner subprocess nous-mêmes.
- **Adversaires/ancres : Petra uniquement.** Les bots communautaires
  sont morts (Hannibal figé en 2017, Arch-AI en 2020, licences
  absentes). Ancres = Petra à plusieurs difficultés + notre hall of
  fame au fil du temps.
- **Ratings : openskill.py** (MIT, actif) plutôt que trueskill
  (dormant, brevet). Archive QD d'appoint si besoin : pyribs (MIT).

## Garde-fous machine (hérités de polytopia)

- Parties bornées (cap de tours + timeout mural) — deux turtles ne
  doivent jamais bloquer un slot.
- Jamais deux campagnes d'évaluation lourdes en même temps.
- Les longs runs se lancent détachés (setsid/nohup), pas accrochés à une
  session d'outil.
- En cas de gel : lire `~/freeze-diag/trace.log` avant de théoriser.
