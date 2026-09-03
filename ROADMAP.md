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

## Phase 4 — Boucle d'évolution (OpenEvolve) — FAIT (2026-09-03)

Décision d'outillage appliquée : OpenEvolve 0.3.2 (pip, venv `.venv`)
avec backend natif `claude_code` (`claude -p`, auth par session OAuth,
modèle sonnet). La cible d'évolution est le `config.js` du bot
`candidate` (régénéré depuis forkbot par `scripts/make_candidate.py`,
dossier gitignoré — état généré).

- [x] `harness/oadlab/evalapi.py` : évaluation programmatique — rend
      les métriques du batch courant (combined_score = moyenne des
      winrates par ancre ; sans-résultat = défaite, non gameable),
      matchs tracés en base sous un tag par version
- [x] `evolution/evaluator.py` : contrat OpenEvolve en cascade —
      stage 1 = 6 parties (~1 min, seuil 0.34), stage 2 = 24 parties ;
      `parallel_evaluations: 1` (le parallélisme est au niveau parties)
- [x] `evolution/config.yaml` : prompt système avec les contraintes
      dures (imports/exports intouchables, champs Config consommés
      ailleurs), 3 îlots, population 60, diff-based
- [x] `evolution/run_night.sh` : lancement détaché (setsid+nohup+
      disown), refus si un run tourne déjà, logs horodatés sous
      `runs/evolution/`
- [x] Smoke test : la chaîne harnais→OpenEvolve est validée (programme
      initial évalué sur 24 parties, métriques remontées, checkpoint et
      best sauvegardés). Les générations LLM échouaient pour deux
      raisons, toutes deux réglées ou identifiées :
      1. `max_budget_usd` non propagé aux modèles → `--max-budget-usd
         None` → échec de chaque appel CLI (corrigé : clé par-modèle) ;
      2. le CLI `claude` n'est pas authentifié en détaché sur cette
         machine (la session Desktop s'authentifie via l'app hôte,
         tokens CLI vides) → **`claude login` requis une fois, action
         utilisateur**, avant toute nuit de run.
- [x] Revue agent : 6 constats, tous appliqués (budget par-modèle,
      retries=1 contre le blocage du worker unique, gitignore,
      winrates groupés par (ancre, difficulté), garde sys.path)
- [x] Première nuit de run : 100 itérations en ~6 h 45 (19:48 → 02:35),
      sans intervention, 20 checkpoints, 8/100 itérations perdues sur
      « No valid diffs » (formatage LLM, acceptable). **Meilleur
      programme : combined_score 0,75 vs 0,417 au départ** (100% vs
      facile, 62,5% vs moyen, 62,5% vs dur — contre 12,5% au départ),
      ordinal openskill 20,6. Trouvé dès l'itération 9 puis plateau :
      la population sature à 0,58-0,75, et 18/24 victoires est proche
      du plafond de résolution d'une éval à 24 parties. Le gagnant
      lâche la course éco contre Petra dur et le pressure tôt
      (casernes à 16 pop, cadence tours/forteresses ×0,92). Run :
      `runs/evolution/2026-09-02_1948/`.

**Sortie** : la boucle tourne 8 h sans intervention et produit un
checkpoint exploitable au matin (meilleur programme + base de matchs).
Enseignement pour la phase 5 : le plateau vient du manque de diversité
ET de la résolution de l'éval — les descripteurs comportementaux et le
hall of fame sont la suite logique, pas un luxe.

## Phase 5 — Archive qualité-diversité — EN COURS (2026-09-03)

Découverte structurante : en partie 100 % IA, commands.txt ne contient
AUCUNE commande (les IA postent leurs ordres directement dans la
simulation). Les descripteurs viennent des séries temporelles du
StatisticsTracker dans metadata.json (un échantillon/30 s de jeu :
unités par classe, valeurs tuées/perdues, ressources, pop, % carte).

- [x] Descripteurs comportementaux (`harness/oadlab/descriptors.py`),
      tous dans [0,1] : `aggression` (précocité du premier sang
      infligé), `boom` (pop à 10 min), `military` (part combattante de
      la production), `map_control` (emprise max). Stockés par match en
      base, moyennés par batch dans les métriques d'évaluation.
- [x] DAG des lignées en base : table `programs` (parent, itération,
      métriques, descripteurs, résumé LLM du diff, code), importée des
      checkpoints OpenEvolve par `scripts/export_lineage.py`
      (idempotent). Nuit 1 importée : 60 programmes, 59 avec parent.
- [x] Sélection potentiel + originalité : MAP-Elites d'OpenEvolve
      branché sur le comportement — `feature_dimensions:
      [aggression, boom]` (grille 8×8), la fitness reste
      combined_score. La nouveauté est mesurée sur le comportement,
      pas sur le code.
- [x] Hall of fame divers (`scripts/make_hof.py`) : N élites à cellule
      comportementale distincte, matérialisées en bots `hofK` +
      manifeste `runs/hof.json` que le harnais ajoute au pool d'éval.
      Piège trouvé au premier essai : les migrations inter-îlots
      d'OpenEvolve copient un programme sous un nouvel id → hof1 ==
      hof2, parties rejouées À L'IDENTIQUE (même seed/aiseed + bots
      identiques : la reproduction exacte existe donc bel et bien) ;
      déduplication par code ajoutée. Validation 12 parties avec pool
      Petra+HOF : 0 échec, wr_hof remonté, descripteurs en base.
- [x] **Fuite de sandbox colmatée** : le provider `claude_code`
      d'OpenEvolve lance `claude -p` sans restriction d'outils, depuis
      le repo — pendant la nuit 1, le LLM générateur a réellement
      ÉDITÉ `forkbot/config.js` et `evolution/initial_config.js`
      (mtimes 21:27 et 23:58, commentaires citant ses propres
      métriques). Il aurait pu éditer l'évaluateur et truquer son
      score. Fichiers restaurés depuis git ; `evolution/bin/claude`
      (wrapper `--disallowedTools Bash,Edit,Write,...`) désormais
      préfixé au PATH par run_night.sh — testé : le CLI répond CANNOT
      à une demande d'écriture. La nuit 1 reste valide : l'évaluateur
      et le harnais n'ont pas été touchés (git status), seuls
      l'étalon et le programme initial étaient contaminés APRÈS coup.
- [ ] Nuit de validation : vérifier que la carte comportementale se
      remplit (occupation de la grille 8×8) et que les scores avec
      pool HOF restent sains.

**Sortie** : la carte comportementale se remplit ; les runs ne
convergent plus vers un style unique.

## Phase 6 — Atelier visuel humain-LLM — EN COURS (2026-09-03)

Spécification complète dans **SPEC.md** (invariants P1-P6, modèle de
données, front, ordre de construction). Décision : l'atelier passe
AVANT de nouvelles campagnes d'expériences — chaque nuit sans
interface ne produit que des logs.

- [x] Le spinal (SPEC.md §4.1) : tables `runs` (provenance : commit,
      config, pool, budget en parties) / `events` (le film d'un run,
      importé du log) / `intents` (les verbes, en attente de la
      boucle) ; protocole d'éval empreinté sur chaque match (P3) ;
      manifeste run.json écrit au lancement ; import idempotent —
      nuit 1 : 274 événements, 2 358 parties comptées depuis matches
      (le log sous-compte : le merge de cascade OpenEvolve écrase le
      games du stage 1) ; WAL + busy_timeout pour la cohabitation
      boucle/front.
- [ ] Serveur local lisant la même SQLite (endpoints JSON, polling —
      SPEC.md §3)
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
