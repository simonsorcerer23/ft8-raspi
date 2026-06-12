#!/usr/bin/env bash
# install.sh — provision a fresh Raspberry Pi OS Lite for the FT8 Appliance
#
# Idempotent. Re-runnable. Reads architecture.md from the checked-out repo
# for context, /etc/ft8-appliance/config.yaml for runtime config.
#
# Expected starting point:
#   * Pi OS Lite 64-bit (bookworm) booted from NVMe
#   * A normal Linux user with sudo for the first install
#   * Repo either cloned or rsynced to that user's ~/ft8-appliance
#   * SSH key from workstation installed in the app user's authorized_keys
#
# Hinweis: dieses Script ist nur für die *erste* Inbetriebnahme nötig.
# Spätere Updates kommen via systemd-Timer `ft8-self-update.timer` aus
# getaggten Releases im GitHub-Repo (siehe docs/self_update.md).

set -euo pipefail

usage() {
    cat >&2 <<'EOF'
usage: sudo ./deploy/install.sh [--user USER] [--dir APP_DIR]

Defaults:
  --dir   repository root containing this script
  --user  SUDO_USER when available, otherwise owner of APP_DIR
EOF
}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
APP_DIR="${FT8_APP_DIR:-${REPO_ROOT}}"
APP_USER="${FT8_APP_USER:-${SUDO_USER:-}}"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --user)
            APP_USER="${2:-}"
            shift 2
            ;;
        --dir)
            APP_DIR="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

APP_DIR="$(realpath -m "${APP_DIR}")"
if [ -z "${APP_USER}" ] && [ -d "${APP_DIR}" ]; then
    APP_USER="$(stat -c '%U' "${APP_DIR}")"
fi

ETC_DIR="/etc/ft8-appliance"
INSTALL_ENV="${ETC_DIR}/install.env"
LOG_DIR="/var/log/ft8-appliance"
LIB_DIR="/var/lib/ft8-appliance"
TILES_DIR="${LIB_DIR}/tiles"

if [ "$(id -u)" -ne 0 ]; then
    echo "install.sh must run as root (sudo)" >&2
    exit 1
fi

if [ ! -d "${APP_DIR}" ]; then
    echo "expected ${APP_DIR} to exist (git clone or rsync the repo there first)" >&2
    exit 1
fi

if [ -z "${APP_USER}" ] || ! id -u "${APP_USER}" >/dev/null 2>&1; then
    echo "expected user '${APP_USER}' to exist on this system" >&2
    exit 1
fi

APP_HOME="$(getent passwd "${APP_USER}" | cut -d: -f6)"
APP_GROUP="$(id -gn "${APP_USER}")"
if [ ! -d "${APP_HOME}" ]; then
    echo "could not determine home directory for user '${APP_USER}'" >&2
    exit 1
fi

# ----------------------------------------------------------------------------
section() { printf '\n\033[1;36m== %s ==\033[0m\n' "$1"; }

section "1/8  APT packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-dev build-essential \
    libasound2-dev \
    hamlib-utils libhamlib-utils \
    gpsd gpsd-clients \
    chrony \
    hostapd dnsmasq nftables \
    network-manager \
    avahi-daemon \
    sqlite3 jq curl git

# ----------------------------------------------------------------------------
section "2/8  Directories"
install -d -o "${APP_USER}" -g "${APP_GROUP}" "${ETC_DIR}" "${LOG_DIR}" "${LIB_DIR}" "${TILES_DIR}"
{
    printf 'APP_USER=%q\n' "${APP_USER}"
    printf 'APP_GROUP=%q\n' "${APP_GROUP}"
    printf 'APP_HOME=%q\n' "${APP_HOME}"
    printf 'APP_DIR=%q\n' "${APP_DIR}"
} > "${INSTALL_ENV}"
chown root:root "${INSTALL_ENV}"
chmod 644 "${INSTALL_ENV}"

# ----------------------------------------------------------------------------
# Reihenfolge wichtig: ft8_lib MUSS vor dem cffi-build da sein, weil
# _build_ft8.py gegen vendor/ft8_lib/libft8.a linkt. Frühere Versionen
# hatten venv/cffi vor ft8_lib stehen und _build_ft8.py meldete
# fälschlich "(already up-to-date)" ohne die .so zu produzieren —
# Service startete dann mit disabled Decode-Pipeline.
section "3/8  ft8_lib (vendored submodule, compile with -fPIC)"
sudo -u "${APP_USER}" env APP_DIR="${APP_DIR}" bash -lc '
    set -e
    cd "${APP_DIR}/vendor/ft8_lib"
    make clean
    make CFLAGS="-O3 -DHAVE_STPCPY -I. -fPIC"
'

# ----------------------------------------------------------------------------
section "4/8  Python venv + dependencies + cffi-Extension"
sudo -u "${APP_USER}" env APP_DIR="${APP_DIR}" bash -lc '
    set -e
    cd "${APP_DIR}/backend"
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip setuptools wheel
    .venv/bin/pip install -e .[hardware]
    .venv/bin/python -m ft8_appliance.decode._build_ft8
'

# ----------------------------------------------------------------------------
section "5/8  Config files"
if [ ! -f "${ETC_DIR}/config.yaml" ]; then
    cat > "${ETC_DIR}/config.yaml" <<'YAML'
operator:
  callsign: DK9XR
bands:
  - { name: "20m", freq_khz: 14074, antenna: endfed_2040 }
  - { name: "40m", freq_khz:  7074, antenna: endfed_2040 }
antennas:
  - { name: endfed_2040, bands: ["20m", "40m"] }
operating:
  swr_max: 2.0
  max_ptt_s: 18
network:
  ap_fallback:
    ssid: ft8-hochgericht
    psk: changeme-please
ui:
  language: de
YAML
    chown "${APP_USER}:${APP_GROUP}" "${ETC_DIR}/config.yaml"
    chmod 640 "${ETC_DIR}/config.yaml"
    echo "wrote default ${ETC_DIR}/config.yaml — EDIT IT before first boot"
fi

# ----------------------------------------------------------------------------
section "6/8  System service files + sudoers"
RENDER_DIR="${APP_DIR}/.deploy-rendered"
APP_USER="${APP_USER}" APP_GROUP="${APP_GROUP}" APP_HOME="${APP_HOME}" APP_DIR="${APP_DIR}" \
    "${APP_DIR}/deploy/render-install-files.sh" "${RENDER_DIR}" "${INSTALL_ENV}"
chown -R "${APP_USER}:${APP_GROUP}" "${RENDER_DIR}"

install -m 644 "${RENDER_DIR}/systemd/ft8-controller.service"    /etc/systemd/system/
install -m 644 "${RENDER_DIR}/systemd/ft8-rigctld.service"       /etc/systemd/system/
install -m 644 "${RENDER_DIR}/systemd/ft8-ap-fallback.service"   /etc/systemd/system/
install -m 644 "${RENDER_DIR}/systemd/ft8-self-update.service"   /etc/systemd/system/
install -m 644 "${RENDER_DIR}/systemd/ft8-self-update.timer"     /etc/systemd/system/

# Sudoers-Snippet für Self-Update + Backend-trigger des Self-Update-Service.
# Scope ist absichtlich minimal: nur 4 spezifische systemctl-Befehle.
install -m 440 "${RENDER_DIR}/sudoers.d/ft8-self-update"             /etc/sudoers.d/ft8-self-update
visudo -c -f /etc/sudoers.d/ft8-self-update >/dev/null

# Render /etc/default/ft8-rigctld from RigConfig in config.yaml so the
# rigctld unit knows which Hamlib model / serial device / baud to launch with.
# Re-run this whenever the operator switches rigs (IC-705 <-> IC-7300 etc.).
sudo -u "${APP_USER}" "${APP_DIR}/backend/.venv/bin/python" -c "
from ft8_appliance.config import load_config
from ft8_appliance.rig import write_rigctld_envfile
cfg = load_config('${ETC_DIR}/config.yaml')
write_rigctld_envfile(cfg.rig, '/etc/default/ft8-rigctld')
print('wrote /etc/default/ft8-rigctld for', cfg.rig.model)
"

install -m 644 "${APP_DIR}/deploy/chrony/ft8-gps.conf"            /etc/chrony/conf.d/
install -m 644 "${APP_DIR}/deploy/dnsmasq/ap-fallback.conf"       /etc/dnsmasq.d/ft8-ap-fallback.conf
install -m 644 "${APP_DIR}/deploy/hostapd/ap.conf"                /etc/hostapd/ft8-ap.conf

install -d /etc/nftables.d
install -m 644 "${APP_DIR}/deploy/nftables/captive-redirect.nft"  /etc/nftables.d/ft8-captive.nft

# ----------------------------------------------------------------------------
section "7/8  gpsd configuration"
# Point gpsd at the u-blox VK-162. /dev/ttyACM0 is the typical kernel name.
cat > /etc/default/gpsd <<'EOF'
START_DAEMON="true"
USBAUTO="true"
DEVICES="/dev/ttyACM0"
GPSD_OPTIONS="-n -G"
GPSD_SOCKET="/var/run/gpsd.sock"
EOF

# ----------------------------------------------------------------------------
section "8/8  Enable + restart services"
systemctl daemon-reload
systemctl enable --now chrony gpsd avahi-daemon NetworkManager
systemctl enable --now ft8-rigctld
systemctl enable --now ft8-controller
systemctl enable --now ft8-self-update.timer

systemctl restart chrony

echo
echo "------------------------------------------------------------"
echo "Install done. UI: http://ft8.local/ (or http://$(hostname -I | awk '{print $1}'):8000/)"
echo "Check:        systemctl status ft8-controller"
echo "Self-Update:  systemctl list-timers ft8-self-update.timer"
echo "Edit:         ${ETC_DIR}/config.yaml"
echo "Logs:         journalctl -u ft8-controller -f"
echo "------------------------------------------------------------"
