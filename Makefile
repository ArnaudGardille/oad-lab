# Raccourcis du labo. Rien d'essentiel ne vit ici : chaque cible est un
# appel direct au CLI du harnais ou à un script de scripts/ — le
# Makefile ne fait que fixer les valeurs par défaut (BOT, SEED…) pour
# ne pas les retaper. `make` seul liste les cibles.
#
# Variables : BOT VS DIFF VS_DIFF SEED SPEED PAIRS ITER RUN CKPT PORT
# Exemples :  make watch BOT=hof1 VS_DIFF=4
#             make eval BOT=watchbot PAIRS=6

PY     := PYTHONPATH=harness .venv/bin/python
OADLAB := $(PY) -m oadlab

BOT     ?= watchbot
VS      ?= petra
DIFF    ?= 3
VS_DIFF ?= 3
SEED    ?= 101
SPEED   ?= 5
PAIRS   ?= 4
ITER    ?= 100
PORT    ?= 8420
RUN     ?=
CKPT    ?=

.PHONY: help eval report selftest snapshot watch watch-bg play stop \
        atelier candidate hof lineage import night

help:
	@sed -n 's/^\([a-z-]*\):.*## /  \1\t/p' $(MAKEFILE_LIST) | expand -t 14

## --- voir et jouer -------------------------------------------------

snapshot:  ## fige le meilleur programme dans un bot stable (BOT, RUN)
	$(OADLAB) snapshot $(BOT) $(if $(RUN),--run $(RUN))

watch:  ## regarde BOT vs VS en observateur (SEED, SPEED, VS_DIFF)
	$(OADLAB) watch $(BOT) --vs $(VS) --diff $(DIFF) --vs-diff $(VS_DIFF) \
		--seed $(SEED) --speed $(SPEED)

watch-bg:  ## idem, détaché : rend la main, s'arrête avec `make stop`
	$(OADLAB) watch $(BOT) --vs $(VS) --diff $(DIFF) --vs-diff $(VS_DIFF) \
		--seed $(SEED) --speed $(SPEED) --detach

play:  ## joue contre BOT, toi joueur 1 (SEED=-1 pour une carte au hasard)
	$(OADLAB) play $(BOT) --diff $(DIFF) --seed $(SEED)

stop:  ## arrête les parties lancées en tâche de fond
	-systemctl --user stop oad-watch oad-play

## --- évaluer -------------------------------------------------------

eval:  ## évalue BOT contre le pool (PAIRS seeds par adversaire)
	$(OADLAB) eval $(BOT) --pairs $(PAIRS)

report:  ## réimprime le rapport de BOT depuis la base
	$(OADLAB) report $(BOT)

selftest:  ## vérifie que le pipeline de parties est exploitable
	$(OADLAB) selftest

## --- boucle et base ------------------------------------------------

night:  ## lance une nuit d'évolution détachée (ITER itérations)
	./evolution/run_night.sh $(ITER)

candidate:  ## régénère le bot candidate depuis forkbot
	$(PY) scripts/make_candidate.py

hof:  ## matérialise le hall of fame depuis CKPT (change le pool d'éval)
	@test -n "$(CKPT)" || { echo "make hof CKPT=runs/evolution/<run>/checkpoints/checkpoint_N"; exit 1; }
	$(PY) scripts/make_hof.py $(CKPT)

lineage:  ## importe le DAG des programmes de CKPT en base
	@test -n "$(CKPT)" || { echo "make lineage CKPT=runs/evolution/<run>/checkpoints/checkpoint_N"; exit 1; }
	$(PY) scripts/export_lineage.py $(CKPT)

import:  ## importe le run RUN (provenance, événements, budget) en base
	@test -n "$(RUN)" || { echo "make import RUN=<horodatage du run>"; exit 1; }
	$(PY) scripts/import_run.py runs/evolution/$(RUN)

atelier:  ## sert l'atelier sur 127.0.0.1 (PORT, défaut 8420)
	$(PY) atelier/server.py $(PORT)
