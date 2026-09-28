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
# auf den alten, toten Knoten (100.64.0.2, seit Tagen offline) — ein
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
    # ... OHNE den Bilderordner qsl/ — der geht als Spiegel, s.u.
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

# Die QSL-Karten (2026-09-16 ff.) sind der erste Bestand, der sich nicht
# wiederbeschaffen laesst: eQSL erlaubt hoechstens sechs Bilder je Minute,
# die 6400 Karten brauchten knapp sechs Tage. Sie gehoeren also gesichert —
# aber nicht jede Nacht neu: Am 2026-09-21 waren es 218 MB, jede Sicherung
# enthielt dieselben JPEGs noch einmal, acht Laeufe lagen bei 1,7 GB. Und
# ein Bild aendert sich nie, nachdem es einmal da ist.
#
# Darum zweigeteilt: das Archiv taeglich und klein (Konfiguration, Logbuch,
# WLAN — wenige MB), die Bilder als Spiegel daneben, der nur Neues holt.
SPIEGEL="${DEST_BASE}/qsl-spiegel"
# So viele Archive bleiben liegen. Der Spiegel ist davon nicht betroffen.
BEHALTEN="${FT8_BACKUP_BEHALTEN:-14}"

# Exit-Codes, damit der Timer Fehler von Abwesenheit trennt:
#   0  alles gesichert
#   75 Station nicht erreichbar, der letzte Stand ist aber juenger als
#      OFFLINE_FRIST_H — kein Fehler, der Pi darf auch mal aus sein
#   1  alles andere: Sicherung unvollstaendig, defekt, oder die Station ist
#      schon so lange weg, dass die letzte Sicherung veraltet
# Bis 2026-09-28 wertete die Unit Exit 1 als Erfolg ("ein Fehlschlag ist
# kein Drama") — und damit auch eine defekte qso.sqlite, fehlende
# Pflichtdateien und einen leeren QSL-Spiegel. Gemeldet wurde nichts.
OFFLINE_FRIST_H="${FT8_BACKUP_OFFLINE_FRIST_H:-48}"

# Nur echte Staende zaehlen: Verzeichnisse mit Zeitstempel UND Archiv.
echte_staende() {
    # Beim allerersten Lauf gibt es das Ziel noch nicht; unter pipefail
    # brach der Offline-Zweig dann wortlos ab, statt es zu sagen.
    [ -d "$DEST_BASE" ] || return 0
    find "$DEST_BASE" -mindepth 1 -maxdepth 1 -type d \
        -regextype posix-extended -regex '.*/[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{4}' \
        -exec test -s '{}/ft8-backup.tgz' ';' -print 2>/dev/null | sort -r
}

umask 077
if ! ssh -o ConnectTimeout=20 -o BatchMode=yes "sebastian@${HOST}" true 2>/dev/null; then
    LETZTER="$(echte_staende | head -1)"
    if [ -n "$LETZTER" ]; then
        ALTER_H=$(( ( $(date +%s) - $(stat -c %Y "$LETZTER/ft8-backup.tgz") ) / 3600 ))
    else
        ALTER_H=999999
    fi
    if [ "$ALTER_H" -lt "$OFFLINE_FRIST_H" ]; then
        echo "== ${HOST} nicht erreichbar — letzter Stand ${ALTER_H} h alt, kein Handlungsbedarf"
        exit 75
    fi
    if [ -z "$LETZTER" ]; then
        echo "!! ${HOST} nicht erreichbar, und unter ${DEST_BASE} liegt noch kein Stand."
    else
        echo "!! ${HOST} nicht erreichbar, und der letzte Stand ist ${ALTER_H} h alt"
        echo "   (Frist ${OFFLINE_FRIST_H} h) — die Sicherung veraltet."
    fi
    exit 1
fi

# Das Ziel erst anlegen, wenn die Station antwortet. Vorher entstand bei
# jedem Offline-Lauf ein leeres Zeitstempel-Verzeichnis — nach zwei Wochen
# Pause haette der naechste erfolgreiche Lauf beim Aufraeumen alle echten
# Staende fuer die leeren geopfert. Scheitert der Lauf danach, raeumt die
# Falle das halbe Verzeichnis wieder weg.
mkdir -p "$DEST"
trap '[ -s "${DEST}/ft8-backup.tgz" ] || rm -rf "$DEST"' EXIT
echo "== Backup von ${HOST} nach ${DEST}"

ssh -o ConnectTimeout=20 "sebastian@${HOST}" \
    ". /etc/ft8-appliance/install.env 2>/dev/null; sudo tar czf /tmp/ft8-backup.tgz --ignore-failed-read --exclude=/var/lib/ft8-appliance/qsl ${PATHS[*]} \"\${APP_DIR}/data/cty.dat\" 2>/dev/null; sudo chown \$(id -un) /tmp/ft8-backup.tgz"
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
    trap 'rm -rf "$PRUEF_DIR"; [ -s "${DEST}/ft8-backup.tgz" ] || rm -rf "$DEST"' EXIT
    if tar xzf "${DEST}/ft8-backup.tgz" -C "$PRUEF_DIR" \
            var/lib/ft8-appliance/ 2>/dev/null; then
        # Als Bedingung, nicht als nackter Befehl: Unter set -e beendete ein
        # Exit 1 der Pruefung sonst das ganze Skript, bevor FAIL gesetzt war.
        if ! python3 - "$PRUEF_DIR/var/lib/ft8-appliance/qso.sqlite" <<'PY'
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
        then
            FAIL=1
        fi
    else
        echo "   WARNUNG qso.sqlite liess sich zur Pruefung nicht entpacken"
    fi
fi
echo "== $(du -h "${DEST}/ft8-backup.tgz" | cut -f1) in ${DEST}/ft8-backup.tgz"

# --- QSL-Bilder: Spiegel statt Kopie ------------------------------------
# Kein --delete: Auf der Station geloeschte Bilder bleiben hier. Ein Backup,
# das Loeschungen mitzieht, ist gegen genau den Fall blind, fuer den man es
# hat. Die Dateien sind unveraenderlich, ein zweiter Lauf holt also nur Neues.
if command -v rsync >/dev/null 2>&1; then
    mkdir -p "$SPIEGEL"
    echo "== Spiegele QSL-Karten"
    rsync -a --info=stats2 "sebastian@${HOST}:/var/lib/ft8-appliance/qsl/" "${SPIEGEL}/" \
        | grep -E "Number of .*files transferred|Total transferred" || true
    ANZ="$(find "$SPIEGEL" -type f -name '*.jpg' | wc -l)"
    echo "   ok   ${ANZ} Karten im Spiegel ($(du -sh "$SPIEGEL" | cut -f1))"
    [ "$ANZ" -gt 0 ] || { echo "!! Spiegel leer"; FAIL=1; }
else
    echo "   WARNUNG rsync fehlt — QSL-Bilder NICHT gesichert"
    FAIL=1
fi

# --- Aufraeumen ---------------------------------------------------------
# Ohne das waechst das Ziel taeglich weiter; am 2026-09-21 lagen dort 1,7 GB,
# davon 1,6 GB dieselben Bilder in acht Ausgaben. Nur automatisch angelegte
# Staende (Zeitstempel) fallen weg — von Hand benannte wie
# "4b-stilllegung-..." bleiben.
# Gezaehlt werden nur Staende mit Archiv (echte_staende); ein leeres
# Verzeichnis darf keinen echten Stand verdraengen.
mapfile -t ALT < <(echte_staende | tail -n +"$((BEHALTEN + 1))")
if [ "${#ALT[@]}" -gt 0 ]; then
    echo "== Raeume ${#ALT[@]} alte Staende ab (behalte ${BEHALTEN})"
    for d in "${ALT[@]}"; do rm -rf "$d"; done
fi
echo "== Ziel belegt jetzt $(du -sh "$DEST_BASE" | cut -f1)"

[ "$FAIL" -eq 0 ] || { echo "!! Backup unvollstaendig"; exit 1; }
