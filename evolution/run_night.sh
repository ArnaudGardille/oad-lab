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

# Consomme les intentions de pilotage (SPEC.md §4.3) : écrit la config
# effective (ordres opérateur dans le system_message) et la graine
# (verbe branch) dans $OUT.
.venv/bin/python evolution/apply_intents.py "$OUT"

# Manifeste de provenance (SPEC.md P2) : qui a produit ce run, depuis
# quel état du repo, avec quelle config et quel pool.
DIRTY=0
[ -n "$(git status --porcelain)" ] && DIRTY=1
HOF=0
[ -f runs/hof.json ] && HOF=1
cat > "$OUT/run.json" <<EOF
{
 "started": $(date +%s),
 "git_commit": "$(git rev-parse --short HEAD)",
 "dirty": $DIRTY,
 "config_sha": "$(sha1sum "$OUT/config.yaml" | cut -c1-12)",
 "hof": $HOF,
 "iterations": $ITER
}
EOF

# Désarme le CLI côté génération : le LLM produit des diffs, il n'agit
# pas. Sans ce wrapper (evolution/bin/claude), l'agent de génération a
# réellement édité forkbot/config.js et initial_config.js pendant la
# nuit du 2026-09-02 — il aurait pu éditer l'évaluateur et truquer son
# score. REAL_CLAUDE est résolu AVANT de préfixer le PATH (récursion).
REAL_CLAUDE="$(command -v claude)"
export REAL_CLAUDE
export PATH="$PWD/evolution/bin:$PATH"

setsid nohup .venv/bin/openevolve-run \
    "$OUT/initial.js" \
    evolution/evaluator.py \
    --config "$OUT/config.yaml" \
    --iterations "$ITER" \
    --output "$OUT" \
    >"$OUT/night.log" 2>&1 &

PID=$!
disown
# Un job backgroundé échappe à set -e : vérifier qu'il survit au
# démarrage, sinon les intentions ont été consommées par un run mort
# (échec bruyant plutôt que silencieux — les re-soumettre via le front).
sleep 3
if ! kill -0 "$PID" 2>/dev/null; then
    echo "openevolve-run est mort au lancement — voir $OUT/night.log ;" >&2
    echo "les intentions consommées par ce run sont à re-soumettre." >&2
    exit 1
fi
echo "run lancé : $ITER itérations, pid $PID (pgid $(ps -o pgid= -p $PID | tr -d ' '))"
echo "log : $OUT/night.log"
