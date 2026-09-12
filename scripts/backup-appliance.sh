#!/usr/bin/env bash
# Sichert alles, was eine laufende Appliance ausmacht und nicht im Repo steht:
# Zugangsdaten, Rig-Feineinstellungen, QSO-DB, WLAN-Profile, Hotspot-Config.
#
# Anlass (2026-09-08): Der Pi 4B fiel ohne Vorwarnung aus, und es existierte
# kein Backup — alle API-Keys haetten neu eingegeben werden muessen.
#
#   ./scripts/backup-appliance.sh [HOST] [ZIELVERZEICHNIS]
#
# Von Hand angestossen heisst: laeuft, wenn jemand daran denkt. Am
# 2026-09-12 lag die letzte Sicherung zwei Tage zurueck, und in diesen
# zwei Tagen waren 144 der 221 QSOs entstanden — 65 % des Logbuchs
# ungesichert, ausgerechnet aus den aktivsten Tagen. Fuer einen taeglichen
# Lauf liegen fertige User-Units bereit:
#
#   mkdir -p ~/.config/systemd/user
#   cp deploy/systemd-user/ft8-backup.{service,timer} ~/.config/systemd/user/
#   systemctl --user daemon-reload
#   systemctl --user enable --now ft8-backup.timer
#
# Sie laufen als Benutzer, brauchen kein root und holen einen verpassten
# Tag nach (Persistent=true). Der Rechner muss dafuer laufen — wer die
# Sicherung unabhaengig davon will, legt den Timer auf den Pi und schiebt
# das Archiv von dort weg.
#
# Default-Ziel liegt ausserhalb des Repos (Secrets gehoeren nicht nach Git).
set -euo pipefail

# Der Tailscale-Name "ft8" zeigt seit dem Umzug auf den Pi 5 noch immer
# auf den alten, toten Knoten (100.96.43.23, seit Tagen offline) — ein
# Aufruf ohne Argument lief dort still ins Timeout. Der laufende Pi
# heisst im Tailnet "ft8-pi5" (2026-09-12).
HOST="${1:-ft8-pi5}"
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

# Zusaetzlich: <APP_DIR>/data/cty.dat — die DXCC-Laenderdatenbank. Sie ist per
# .gitignore vom Repo ausgeschlossen, ein frischer Klon hat sie also nicht.
# Ohne sie faellt die komplette Laenderzuordnung aus: keine Flaggen in den
# Pushes, "0 DXCCs" in der QRZ-Statistik, und die Antwortstrategie kann
# Laender-Neuheit nicht mehr bewerten. Beim Umzug auf den Pi 5 am 2026-09-09
# fiel das erst im Betrieb auf. Der Pfad kommt aus install.env, weil das
# App-Verzeichnis nicht ueberall gleich heisst.

umask 077
mkdir -p "$DEST"
echo "== Backup von ${HOST} nach ${DEST}"

ssh -o ConnectTimeout=20 "sebastian@${HOST}" \
    ". /etc/ft8-appliance/install.env 2>/dev/null; sudo tar czf /tmp/ft8-backup.tgz --ignore-failed-read ${PATHS[*]} \"\${APP_DIR}/data/cty.dat\" 2>/dev/null; sudo chown \$(id -un) /tmp/ft8-backup.tgz"
scp -q "sebastian@${HOST}:/tmp/ft8-backup.tgz" "${DEST}/"
ssh "sebastian@${HOST}" 'rm -f /tmp/ft8-backup.tgz'
chmod 600 "${DEST}/ft8-backup.tgz"

# Verifizieren statt hoffen: die drei Dateien, ohne die ein Neuaufbau weh tut.
echo "== Pruefe Inhalt"
# Liste einmal materialisieren: "tar | grep -q" beendet grep frueh, tar stirbt
# an SIGPIPE und pipefail wertet die Pipeline als Fehler — die Pruefung meldete
# dann "FEHLT" fuer Dateien, die im Archiv liegen.
LIST="$(tar tzf "${DEST}/ft8-backup.tgz")"
case "$LIST" in
    *data/cty.dat*) echo "   ok   data/cty.dat (DXCC-Laenderdatenbank)" ;;
    *) echo "   FEHLT data/cty.dat — ohne sie keine Laenderzuordnung auf dem Zielsystem" ;;
esac
FAIL=0
for f in etc/ft8-appliance/config.yaml var/lib/ft8-appliance/runtime_state.json var/lib/ft8-appliance/qso.sqlite; do
    if grep -qx "$f" <<<"$LIST"; then
        echo "   ok   $f"
    else
        echo "   FEHLT $f"
        FAIL=1
    fi
done
WLAN=$(grep -c "system-connections/.*\.nmconnection" <<<"$LIST" || true)
echo "   WLAN-Profile: ${WLAN}"

# Vorhanden ist nicht dasselbe wie heil. qso.sqlite wird im laufenden
# Betrieb weggeschrieben, waehrend tar sie liest — Hauptdatei und WAL
# stammen also aus zwei verschiedenen Augenblicken. Meist traegt SQLite
# das beim Oeffnen zusammen, aber "meist" ist fuer ein Backup zu wenig.
# Am 2026-09-12 war die Kopie nachweislich in Ordnung; geprueft wird es
# ab jetzt bei jedem Lauf, denn ein Backup faellt sonst erst an dem Tag
# auf, an dem man es braucht.
if [ "$FAIL" -eq 0 ] && command -v python3 >/dev/null 2>&1; then
    PRUEF_DIR="$(mktemp -d)"
    trap 'rm -rf "$PRUEF_DIR"' EXIT
    if tar xzf "${DEST}/ft8-backup.tgz" -C "$PRUEF_DIR" \
            var/lib/ft8-appliance/ 2>/dev/null; then
        python3 - "$PRUEF_DIR/var/lib/ft8-appliance/qso.sqlite" <<'PY'
import sqlite3, sys
try:
    con = sqlite3.connect(sys.argv[1])
    ergebnis = con.execute("pragma integrity_check").fetchone()[0]
    n = con.execute("select count(*) from qso").fetchone()[0]
    con.close()
except Exception as exc:
    print(f"   FEHLER qso.sqlite nicht lesbar: {exc}")
    raise SystemExit(1)
if ergebnis != "ok":
    print(f"   DEFEKT qso.sqlite: {ergebnis}")
    raise SystemExit(1)
print(f"   ok   qso.sqlite ist heil ({n} QSOs)")
PY
        [ $? -eq 0 ] || FAIL=1
    else
        echo "   WARNUNG qso.sqlite liess sich zur Pruefung nicht entpacken"
    fi
fi
echo "== $(du -h "${DEST}/ft8-backup.tgz" | cut -f1) in ${DEST}/ft8-backup.tgz"
[ "$FAIL" -eq 0 ] || { echo "!! Backup unvollstaendig"; exit 1; }
