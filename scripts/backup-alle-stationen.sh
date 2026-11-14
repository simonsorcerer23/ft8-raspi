#!/usr/bin/env bash
# Sichert alle FT8-Stationen nacheinander, jede in ihr eigenes Ziel.
#
# Seit 2026-11-14: Der taegliche Timer rief backup-appliance.sh ohne Argument
# auf und sicherte damit nur ft8-pi5. Der MK2-Pi (ft8-mk2) loggt seit dem
# 12.11. QSOs, hatte in zwei Tagen drei NVMe-Ausfaelle und war nirgends
# gesichert.
#
# Aufruf:
#   ./scripts/backup-alle-stationen.sh              # Vorgabe: ft8-pi5 ft8-mk2
#   FT8_BACKUP_HOSTS="ft8-pi5" ./scripts/backup-alle-stationen.sh
#
# Exit-Code wie backup-appliance.sh, der schlechteste aller Stationen:
#   1 (Fehler) vor 75 (nicht erreichbar, letzter Stand noch frisch) vor 0.
# Eine fehlgeschlagene Station haelt die naechste nicht auf.
set -uo pipefail

HIER="$(cd "$(dirname "$0")" && pwd)"
ERGEBNIS=0
for HOST in ${FT8_BACKUP_HOSTS:-ft8-pi5 ft8-mk2}; do
    echo "######## ${HOST}"
    "${HIER}/backup-appliance.sh" "${HOST}"
    RC=$?
    case "$RC" in
        0) ;;
        75) [ "$ERGEBNIS" -eq 0 ] && ERGEBNIS=75 ;;
        *) ERGEBNIS=1 ;;
    esac
done
exit "$ERGEBNIS"
