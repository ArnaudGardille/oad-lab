#!/usr/bin/env bash
# Lance une nuit d'évolution, détachée de la session (setsid + nohup) :
# le run survit à la fermeture du terminal ou de la session d'outil.
#
#   ./evolution/run_night.sh [iterations]
#
# Suivi : tail -f runs/evolution/<horodatage>/night.log
# Arrêt : kill -TERM -<pgid>  (pgid affiché au lancement)

set -euo pipefail
cd "$(dirname "$0")/.."

ITER="${1:-100}"
STAMP="$(date +%Y-%m-%d_%H%M)"
OUT="runs/evolution/$STAMP"
mkdir -p "$OUT"

if pgrep -f "openevolve-run" >/dev/null; then
    echo "un run openevolve tourne déjà — refus de lancer un second" >&2
    exit 1
fi

python3 scripts/make_candidate.py

setsid nohup .venv/bin/openevolve-run \
    evolution/initial_config.js \
    evolution/evaluator.py \
    --config evolution/config.yaml \
    --iterations "$ITER" \
    --output "$OUT" \
    >"$OUT/night.log" 2>&1 &

PID=$!
disown
echo "run lancé : $ITER itérations, pid $PID (pgid $(ps -o pgid= -p $PID | tr -d ' '))"
echo "log : $OUT/night.log"
