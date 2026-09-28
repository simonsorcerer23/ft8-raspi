#!/usr/bin/env bash
# Controller unter einem eigenen Dienstbenutzer ohne sudo betreiben.
#
#   sudo deploy/dienstbenutzer-einrichten.sh            # umstellen
#   sudo deploy/dienstbenutzer-einrichten.sh --zurueck  # alter Zustand
#
# Anlass (2026-09-28): Der Controller — und mit ihm die Weboberflaeche —
# lief als der Anmeldebenutzer, und der hatte "NOPASSWD: ALL". Wer die
# Weboberflaeche uebernahm, war sofort root. Die schmalen sudoers-Regeln
# und die Pruefungen im Self-Update waren daneben bedeutungslos.
#
# Danach gilt:
#   * Der Controller laeuft als SERVICE_USER (Vorgabe "ft8"), Systembenutzer
#     ohne Anmeldung und ohne sudo — bis auf die Befehle, die die Oberflaeche
#     ausloest (deploy/sudoers.d/ft8-self-update.in, zweiter Block).
#   * Der Code bleibt beim APP_USER. Der Dienstbenutzer darf ihn lesen und
#     ausfuehren, nicht aendern. Self-Update laeuft weiter als APP_USER.
#   * /etc/ft8-appliance ist root-eigen mit Sticky-Bit: Der Dienstbenutzer
#     schreibt config.yaml, kann install.env aber weder aendern noch ersetzen.
#     (install.env wird von Skripten gelesen, die mit root-Rechten laufen.)
#   * /var/lib/ft8-appliance gehoert dem Dienstbenutzer.
#
# Idempotent: ein zweiter Lauf aendert nichts mehr. Die Station ist waehrend
# des Neustarts des Controllers etwa 20 s still.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "als root starten (sudo)"; exit 1; }

INSTALL_ENV=/etc/ft8-appliance/install.env
# shellcheck disable=SC1090
. "${INSTALL_ENV}"
: "${APP_USER:?APP_USER fehlt in ${INSTALL_ENV}}"
: "${APP_DIR:?APP_DIR fehlt in ${INSTALL_ENV}}"
DIENST="${FT8_SERVICE_USER:-ft8}"
ETC=/etc/ft8-appliance
LIB=/var/lib/ft8-appliance
LOG=/var/log/ft8-appliance
DIENST_HOME="${LIB}/home"
APP_HOME="$(getent passwd "${APP_USER}" | cut -d: -f6)"

setze_env() {   # setze_env NAME WERT   (WERT leer = Zeile entfernen)
    local tmp
    tmp="$(mktemp)"
    grep -v "^$1=" "${INSTALL_ENV}" > "${tmp}" || true
    [ -n "$2" ] && printf '%s=%q\n' "$1" "$2" >> "${tmp}"
    install -m 644 -o root -g root "${tmp}" "${INSTALL_ENV}"
    rm -f "${tmp}"
}

units_neu() {
    local r
    r="$(mktemp -d)"
    "${APP_DIR}/deploy/render-install-files.sh" "${r}" "${INSTALL_ENV}"
    visudo -c -f "${r}/sudoers.d/ft8-self-update" >/dev/null
    install -m 440 -o root -g root "${r}/sudoers.d/ft8-self-update" /etc/sudoers.d/ft8-self-update
    install -m 644 -o root -g root "${r}/systemd/ft8-controller.service" /etc/systemd/system/ft8-controller.service
    # rigctld mit: Seine Argumente stammen aus der config.yaml des Dienstbenutzers.
    install -m 644 -o root -g root "${r}/systemd/ft8-rigctld.service" /etc/systemd/system/ft8-rigctld.service
    # Das Self-Update rendert beim naechsten Lauf in sein eigenes Verzeichnis;
    # dort soll dasselbe liegen wie hier.
    install -d -o "${APP_USER}" "${APP_DIR}/.deploy-rendered"
    cp -r "${r}/." "${APP_DIR}/.deploy-rendered/"
    chown -R "${APP_USER}" "${APP_DIR}/.deploy-rendered"
    rm -rf "${r}"
    systemctl daemon-reload
}

if [ "${1:-}" = "--zurueck" ]; then
    echo "== Zurueck auf ${APP_USER}"
    systemctl stop ft8-controller
    setze_env SERVICE_USER ""
    setze_env SERVICE_GROUP ""
    chown -R "${APP_USER}:${APP_USER}" "${LIB}" "${LOG}"
    chown "${APP_USER}:${APP_USER}" "${ETC}"
    chmod 755 "${ETC}"
    find "${ETC}" -maxdepth 1 -name 'config.yaml*' -user "${DIENST}" -exec chown "${APP_USER}:${APP_USER}" {} +
    rm -rf /dev/shm/ft8-jt9
    units_neu
    systemctl restart ft8-rigctld
    systemctl start ft8-controller
    echo "== fertig — Controller und rigctld laufen wieder als ${APP_USER}"
    exit 0
fi

echo "== Dienstbenutzer ${DIENST}"
if ! id -u "${DIENST}" >/dev/null 2>&1; then
    useradd --system --home-dir "${DIENST_HOME}" --no-create-home \
        --shell /usr/sbin/nologin --user-group "${DIENST}"
fi
# Ausdruecklich NICHT in sudo, adm oder der Gruppe des APP_USER: Das
# Repo-Verzeichnis ist gruppenschreibbar.
for g in sudo adm "$(id -gn "${APP_USER}")"; do
    gpasswd -d "${DIENST}" "${g}" >/dev/null 2>&1 || true
done

echo "== Code lesbar, nicht schreibbar"
# 711: durchqueren ja, auflisten nein. Mehr braucht der Dienstbenutzer nicht.
chmod 711 "${APP_HOME}"
chmod -R o+rX "${APP_DIR}"
# git verweigert fremde Repos ("dubious ownership"); die Oberflaeche liest
# daraus nur die Version.
git config --system --get-all safe.directory 2>/dev/null | grep -qx "${APP_DIR}" \
    || git config --system --add safe.directory "${APP_DIR}"

echo "== Daten"
install -d -m 750 -o "${DIENST}" -g "${DIENST}" "${DIENST_HOME}"
chown -R "${DIENST}:${DIENST}" "${LIB}" "${LOG}"
# LoTW: tqsl sucht Zertifikat und Station Location in ~/.tqsl des laufenden
# Benutzers.
if [ -d "${APP_HOME}/.tqsl" ] && [ ! -d "${DIENST_HOME}/.tqsl" ]; then
    cp -a "${APP_HOME}/.tqsl" "${DIENST_HOME}/.tqsl"
    chown -R "${DIENST}:${DIENST}" "${DIENST_HOME}/.tqsl"
fi

echo "== Konfiguration: schreibbar, install.env geschuetzt"
chown root:"${DIENST}" "${ETC}"
chmod 1775 "${ETC}"
chown root:root "${INSTALL_ENV}"
chmod 644 "${INSTALL_ENV}"
for f in "${ETC}/config.yaml" "${ETC}/config.yaml.bak"; do
    [ -f "$f" ] && { chown "${DIENST}:${DIENST}" "$f"; chmod 600 "$f"; }
done

echo "== Units und sudoers"
systemctl stop ft8-controller
setze_env SERVICE_USER "${DIENST}"
setze_env SERVICE_GROUP "${DIENST}"
# jt9 arbeitet in /dev/shm/ft8-jt9 — das Verzeichnis gehoerte bisher dem
# APP_USER. tmpfs, wird neu angelegt.
rm -rf /dev/shm/ft8-jt9
units_neu
systemctl restart ft8-rigctld
systemctl start ft8-controller
echo "== fertig — Controller und rigctld laufen als ${DIENST}"
