#!/usr/bin/env bash
# Filet de sécurité mémoire : tue les parties 0 A.D. headless (et
# uniquement elles : --autostart-nonvisual) plus vieilles que MAX_AGE
# secondes, bien au-delà du GAME_TIMEOUT de 240 s du harnais.
#
# Nécessaire parce qu'un harnais lancé depuis une session confinée
# (label AppArmor claude-desktop) reçoit EPERM sur kill() vers les
# processus snap : chaque partie en timeout survivrait indéfiniment
# (3-10 Go de RSS chacune — c'est ce qui a saturé les 128 Go les
# 2026-09-03/04). Le reaper doit donc tourner unconfined :
#
#   systemd-run --user --collect --unit=oadlab-reaper \
#       "$PWD/scripts/reap_hung_games.sh"
#
# Suivi : journalctl --user -u oadlab-reaper
# Arrêt : systemctl --user stop oadlab-reaper
MAX_AGE="${1:-600}"
while true; do
    for pid in $(pgrep -f -- '--autostart-nonvisual'); do
        et="$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' ')"
        if [ -n "$et" ] && [ "$et" -gt "$MAX_AGE" ]; then
            kill -KILL "$pid" 2>/dev/null \
                && echo "reaped $pid (age ${et}s)"
        fi
    done
    sleep 60
done
