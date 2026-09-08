#!/usr/bin/env bash
# Sichert alles, was eine laufende Appliance ausmacht und nicht im Repo steht:
# Zugangsdaten, Rig-Feineinstellungen, QSO-DB, WLAN-Profile, Hotspot-Config.
#
# Anlass (2026-09-08): Der Pi 4B fiel ohne Vorwarnung aus, und es existierte
# kein Backup — alle API-Keys haetten neu eingegeben werden muessen.
#
#   ./scripts/backup-appliance.sh [HOST] [ZIELVERZEICHNIS]
#
# Default-Ziel liegt ausserhalb des Repos (Secrets gehoeren nicht nach Git).
set -euo pipefail

HOST="${1:-ft8}"
DEST_BASE="${2:-$HOME/.config/codex/secrets/ft8-backup}"
STAMP="$(date +%Y-%m-%d_%H%M)"
DEST="${DEST_BASE}/${STAMP}"

# Was gesichert wird. --ignore-failed-read: fehlende Pfade sind kein Fehler
# (z.B. hostapd erst ab v0.67, gpsd nur mit GPS-Dongle).
PATHS=(
    /etc/ft8-appliance                      # config.yaml (alle Zugangsdaten), install.env
    /var/lib/ft8-appliance                  # qso.sqlite + DB-Backups, runtime_state.json (Audio-Gain!)
    /etc/hostapd                            # AP-Fallback inkl. PSK
    /etc/NetworkManager/system-connections   # WLAN-Profile inkl. PSKs
    /etc/chrony/chrony.conf
    /etc/default/gpsd
    /boot/firmware/config.txt
)

umask 077
mkdir -p "$DEST"
echo "== Backup von ${HOST} nach ${DEST}"

ssh -o ConnectTimeout=20 "sebastian@${HOST}" \
    "sudo tar czf /tmp/ft8-backup.tgz --ignore-failed-read ${PATHS[*]} 2>/dev/null; sudo chown \$(id -un) /tmp/ft8-backup.tgz"
scp -q "sebastian@${HOST}:/tmp/ft8-backup.tgz" "${DEST}/"
ssh "sebastian@${HOST}" 'rm -f /tmp/ft8-backup.tgz'
chmod 600 "${DEST}/ft8-backup.tgz"

# Verifizieren statt hoffen: die drei Dateien, ohne die ein Neuaufbau weh tut.
echo "== Pruefe Inhalt"
FAIL=0
for f in etc/ft8-appliance/config.yaml var/lib/ft8-appliance/runtime_state.json var/lib/ft8-appliance/qso.sqlite; do
    if tar tzf "${DEST}/ft8-backup.tgz" | grep -qx "$f"; then
        echo "   ok   $f"
    else
        echo "   FEHLT $f"
        FAIL=1
    fi
done
WLAN=$(tar tzf "${DEST}/ft8-backup.tgz" | grep -c "system-connections/.*\.nmconnection" || true)
echo "   WLAN-Profile: ${WLAN}"
echo "== $(du -h "${DEST}/ft8-backup.tgz" | cut -f1) in ${DEST}/ft8-backup.tgz"
[ "$FAIL" -eq 0 ] || { echo "!! Backup unvollstaendig"; exit 1; }
