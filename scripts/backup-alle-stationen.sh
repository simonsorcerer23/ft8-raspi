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
#   FT8_BACKUP_ZEITWEISE="" ./scripts/backup-alle-stationen.sh   # auch ft8-pi5 mit 48-h-Frist
#
# Exit-Code wie backup-appliance.sh, der schlechteste aller Stationen:
#   1 (Fehler) vor 75 (nicht erreichbar, letzter Stand noch frisch) vor 0.
# Eine fehlgeschlagene Station haelt die naechste nicht auf.
#
# Seit 2026-11-27: Stationen in FT8_BACKUP_ZEITWEISE (Vorgabe ft8-pi5) duerfen
# beliebig lange aus sein. Der Pi 5 steht nicht bei uns und wird absichtlich
# ausgeschaltet (27.11.2026, Sebastian: „Ft8 kontrolliert Ray ist absichtlich aus“). Einen langen
# Ausfall meldet dort schon dk9xr-spiegel nach 3 Tagen (altec-server-setup
# ops/automatik/ziele.yaml, `zeitweise`); die Sicherung meldete denselben Ausfall
# nach 48 h jede Nacht noch einmal als Fehler. Ist die Station an, wird sie wie
# jede andere gesichert und geprueft.
set -uo pipefail

HIER="$(cd "$(dirname "$0")" && pwd)"
ERGEBNIS=0
for HOST in ${FT8_BACKUP_HOSTS:-ft8-pi5 ft8-mk2}; do
    echo "######## ${HOST}"
    case " ${FT8_BACKUP_ZEITWEISE-ft8-pi5} " in
        *" ${HOST} "*) FT8_BACKUP_OFFLINE_FRIST_H=999999 "${HIER}/backup-appliance.sh" "${HOST}" ;;
        *) "${HIER}/backup-appliance.sh" "${HOST}" ;;
    esac
    RC=$?
    case "$RC" in
        0) ;;
        75) [ "$ERGEBNIS" -eq 0 ] && ERGEBNIS=75 ;;
        *) ERGEBNIS=1 ;;
    esac
done
exit "$ERGEBNIS"
