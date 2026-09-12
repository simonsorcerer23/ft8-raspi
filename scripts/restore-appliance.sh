#!/usr/bin/env bash
# Spielt ein Backup von backup-appliance.sh auf eine FRISCH installierte
# Appliance ein (deploy/install.sh muss dort schon gelaufen sein).
#
#   ./scripts/restore-appliance.sh <backup.tgz> [HOST]
#
# Was NICHT eingespielt wird und warum:
#   install.env  - gehoert zur neuen Installation (Pfade/User koennen abweichen)
#   config.txt   - Pi-Modell-spezifisch (Pi 5 hat andere Boot-Parameter)
#   tailscale    - Neuanmeldung ist einfacher und sauberer als State-Kopie
set -euo pipefail

TGZ="${1:?Backup-Archiv angeben: ./scripts/restore-appliance.sh <backup.tgz> [host]}"
# s. backup-appliance.sh: "ft8" ist der alte, tote Knoten.
HOST="${2:-ft8-pi5}"
[ -f "$TGZ" ] || { echo "Archiv nicht gefunden: $TGZ"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
tar xzf "$TGZ" -C "$WORK"

CFG="${WORK}/etc/ft8-appliance/config.yaml"
[ -f "$CFG" ] || { echo "config.yaml fehlt im Archiv"; exit 1; }

# Die AP-SSID stand am 6.9. nur in der hostapd-Conf, nicht in der config.yaml
# (von Hand geaendert). install.sh rendert hostapd aus der config.yaml — ohne
# diese Korrektur hiesse der Hotspot nach dem Neuaufbau wieder anders.
if grep -q "ssid: ft8-hochgericht" "$CFG"; then
    sed -i 's/ssid: ft8-hochgericht/ssid: ft8-hotspot/' "$CFG"
    echo "== AP-SSID in config.yaml auf ft8-hotspot korrigiert"
fi

echo "== Stoppe Controller auf ${HOST}"
ssh -o ConnectTimeout=20 "sebastian@${HOST}" 'sudo systemctl stop ft8-controller 2>/dev/null || true'

echo "== Kopiere Konfiguration und Daten"
tar czf "${WORK}/payload.tgz" -C "$WORK" \
    etc/ft8-appliance/config.yaml \
    var/lib/ft8-appliance \
    $([ -d "${WORK}/etc/hostapd" ] && echo etc/hostapd) \
    $([ -d "${WORK}/etc/NetworkManager/system-connections" ] && echo etc/NetworkManager/system-connections)
scp -q "${WORK}/payload.tgz" "sebastian@${HOST}:/tmp/ft8-restore.tgz"

# cty.dat separat: im Archiv liegt sie unter dem App-Verzeichnis des
# Quellsystems, auf dem Ziel kann das anders heissen. Darum suchen,
# einzeln uebertragen und drueben aus install.env platzieren.
CTY_SRC="$(find "$WORK" -path '*/data/cty.dat' -type f 2>/dev/null | head -1)"
if [ -n "$CTY_SRC" ]; then
    scp -q "$CTY_SRC" "sebastian@${HOST}:/tmp/cty.dat"
else
    echo "== HINWEIS: keine cty.dat im Backup — auf dem Ziel gibt es dann keine"
    echo "   Laenderzuordnung (keine Flaggen, 0 DXCCs). Datei nachtragen unter"
    echo "   <APP_DIR>/data/cty.dat."
fi

ssh -o ConnectTimeout=20 "sebastian@${HOST}" 'bash -s' <<'REMOTE'
set -euo pipefail
sudo tar xzf /tmp/ft8-restore.tgz -C / --no-same-owner
rm -f /tmp/ft8-restore.tgz

# Eigentuemer/Rechte geradeziehen: NetworkManager verweigert Profile, die
# nicht root:root 0600 sind; die App laeuft als APP_USER.
APP_USER="$(. /etc/ft8-appliance/install.env 2>/dev/null && echo "${APP_USER:-sebastian}")"
sudo chown -R "${APP_USER}:${APP_USER}" /var/lib/ft8-appliance /etc/ft8-appliance
sudo chown root:root /etc/NetworkManager/system-connections/*.nmconnection 2>/dev/null || true
sudo chmod 600 /etc/NetworkManager/system-connections/*.nmconnection 2>/dev/null || true
sudo nmcli connection reload 2>/dev/null || true

# cty.dat an den Platz legen, den der Orchestrator beim Start liest.
if [ -f /tmp/cty.dat ]; then
    APP_DIR="$(. /etc/ft8-appliance/install.env 2>/dev/null && echo "${APP_DIR:-/home/sebastian/ft8-raspi}")"
    sudo install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_DIR}/data"
    sudo install -m 644 -o "${APP_USER}" -g "${APP_USER}" /tmp/cty.dat "${APP_DIR}/data/cty.dat"
    rm -f /tmp/cty.dat
    echo "cty.dat nach ${APP_DIR}/data/ eingespielt"
fi

sudo systemctl start ft8-controller
REMOTE

echo "== Warte auf die API"
for _ in $(seq 1 30); do
    if ssh "sebastian@${HOST}" 'curl -fsS -m 3 localhost:8000/api/status >/dev/null 2>&1'; then break; fi
    sleep 2
done
ssh "sebastian@${HOST}" 'curl -s -m 5 localhost:8000/api/status | python3 -c "
import sys, json
s = json.load(sys.stdin)
print(\"Callsign:\", s.get(\"callsign\"), \"| State:\", s.get(\"state\"), \"| TX:\", s.get(\"tx_power_w\"), \"W\")
r = s.get(\"rig\") or {}
print(\"Rig:\", r.get(\"freq_hz\"), r.get(\"mode\"), \"| Antenne:\", s.get(\"active_antenna\"), \"| QSOs:\", s.get(\"worked_count\"))"'
echo "== Restore fertig. Offen: Tailscale anmelden (sudo tailscale up), Leistung pruefen."
