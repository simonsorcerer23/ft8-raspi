#!/usr/bin/env bash
# Render systemd units and sudoers from install-time APP_* values.
#
# install.sh writes /etc/ft8-appliance/install.env. self-update.sh reads that
# file and re-renders the same concrete paths before syncing system files.

set -euo pipefail

OUT_DIR="${1:?usage: render-install-files.sh OUT_DIR [INSTALL_ENV]}"
INSTALL_ENV="${2:-/etc/ft8-appliance/install.env}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"

if [ -f "${INSTALL_ENV}" ]; then
    # shellcheck disable=SC1090
    . "${INSTALL_ENV}"
fi

: "${APP_USER:?APP_USER missing}"
: "${APP_DIR:?APP_DIR missing}"

APP_HOME="${APP_HOME:-$(getent passwd "${APP_USER}" | cut -d: -f6)}"
APP_GROUP="${APP_GROUP:-$(id -gn "${APP_USER}")}"

if [ -z "${APP_HOME}" ] || [ -z "${APP_GROUP}" ]; then
    echo "could not resolve APP_HOME/APP_GROUP for ${APP_USER}" >&2
    exit 1
fi

mkdir -p "${OUT_DIR}/systemd" "${OUT_DIR}/sudoers.d"

sed_escape() {
    printf '%s' "$1" | sed -e 's/[|&]/\\&/g'
}

render() {
    local src="$1"
    local dst="$2"
    sed \
        -e "s|@APP_USER@|$(sed_escape "${APP_USER}")|g" \
        -e "s|@APP_GROUP@|$(sed_escape "${APP_GROUP}")|g" \
        -e "s|@APP_HOME@|$(sed_escape "${APP_HOME}")|g" \
        -e "s|@APP_DIR@|$(sed_escape "${APP_DIR}")|g" \
        "${src}" > "${dst}"
}

render "${SCRIPT_DIR}/systemd/ft8-controller.service.in" \
    "${OUT_DIR}/systemd/ft8-controller.service"
render "${SCRIPT_DIR}/systemd/ft8-rigctld.service.in" \
    "${OUT_DIR}/systemd/ft8-rigctld.service"
render "${SCRIPT_DIR}/systemd/ft8-ap-fallback.service.in" \
    "${OUT_DIR}/systemd/ft8-ap-fallback.service"
render "${SCRIPT_DIR}/systemd/ft8-self-update.service.in" \
    "${OUT_DIR}/systemd/ft8-self-update.service"
render "${SCRIPT_DIR}/systemd/ft8-self-update.timer.in" \
    "${OUT_DIR}/systemd/ft8-self-update.timer"
render "${SCRIPT_DIR}/sudoers.d/ft8-self-update.in" \
    "${OUT_DIR}/sudoers.d/ft8-self-update"
