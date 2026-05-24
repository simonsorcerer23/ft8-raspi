#!/usr/bin/env bash
# self-update.sh — pull latest tagged release from GitHub, install, restart.
#
# Aufgerufen von ft8-self-update.service (alle ~10 min via Timer, oder
# manuell durch Backend-API-Trigger via UI-Button).
#
# Verhalten:
#  1. git fetch --tags
#  2. Bestimme aktuellen Tag (oder "untagged" wenn Pi noch auf einem Commit
#     ohne Tag steht) + neuesten verfügbaren semver-Tag.
#  3. Wenn neuester == aktueller → exit 0 still.
#  4. Wenn neuer Tag verfügbar → idle-check via `curl /api/status`.
#     * Wenn TX läuft oder QSO aktiv → skip + ntfy 🟡 + exit 0.
#     * Wenn idle → durchziehen.
#  5. Markiere aktuellen Stand als rollback-target (`ROLLBACK_REF`).
#  6. `git checkout <new_tag>`, `pip install -e .[hardware]` falls deps
#     sich geändert haben, `sudo systemctl restart ft8-controller`.
#  7. Health-Probe: 10s warten, dann curl /api/healthcheck. Wenn nicht
#     "green"/"yellow" → Rollback (checkout zurück + pip + restart) + ntfy
#     🟡 ("rolled back"). Wenn auch Rollback fail → ntfy 🔴.
#  8. Bei Erfolg → ntfy 🟢.
#
# Idempotent. Sicher gegen mehrfache parallele Aufrufe (flock).

set -euo pipefail

# -----------------------------------------------------------------------------
# Konfiguration
APP_DIR="/home/sebastian/ft8-appliance"
API_BASE="http://127.0.0.1:8000/api"
NTFY_SERVER="https://ntfy.sh"
NTFY_TOPIC="ft8-system-$(hostname)"
LOCK_FILE="/tmp/ft8-self-update.lock"
HEALTH_WAIT_S=12      # nach restart so viele Sekunden warten, dann healthcheck
HEALTH_RETRIES=3
HEALTH_RETRY_DELAY_S=4

# -----------------------------------------------------------------------------
log() { printf '[self-update] %s\n' "$*"; }
die() { log "FATAL: $*"; ntfy "🔴" "Self-Update FATAL: $*" || true; exit 1; }

# Fire-and-forget ntfy post. Tolerant — wenn ntfy down ist, blockt es uns
# nicht. (curl -m 5 max 5s, --silent + --show-error.)
ntfy() {
    local icon="$1"; shift
    local msg="$*"
    local title="ft8 self-update ($(hostname))"
    curl -fsS -m 5 \
        -H "Title: ${title}" \
        -H "Tags: ${icon}" \
        -d "${msg}" \
        "${NTFY_SERVER}/${NTFY_TOPIC}" \
        >/dev/null 2>&1 \
        || log "warn: ntfy push failed (continuing)"
}

# Sicherstellen dass nur eine Instanz läuft (Timer + UI-Button-Trigger
# könnten in seltenen Edge-Cases überlappen).
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    log "another self-update already running (lock ${LOCK_FILE}), exit"
    exit 0
fi

cd "${APP_DIR}"

# -----------------------------------------------------------------------------
# Vorbedingungen
if [ ! -d ".git" ]; then
    die "${APP_DIR} ist kein git-Workdir — bitte einmalige Migration via docs/self_update.md durchführen"
fi

# -----------------------------------------------------------------------------
# Tags holen
log "git fetch --tags"
if ! git fetch --tags --prune --quiet 2>&1 | tee -a /tmp/ft8-self-update.fetch.log; then
    # Network/SSH-Fehler ist normaler Failure-Modus (Pi hat kein Netz
    # gerade etc.) — kein ntfy-Alarm dafür.
    log "git fetch failed, skip this round"
    exit 0
fi

CURRENT_TAG="$(git describe --tags --exact-match HEAD 2>/dev/null || echo '')"
CURRENT_DESC="$(git describe --tags --always --dirty 2>/dev/null || echo 'unknown')"
LATEST_TAG="$(git tag -l 'v*' --sort=-v:refname | head -1)"

log "current=${CURRENT_DESC} latest=${LATEST_TAG:-<none>}"

if [ -z "${LATEST_TAG}" ]; then
    log "no semver tags found in repo — nothing to do"
    exit 0
fi

if [ "${CURRENT_TAG}" = "${LATEST_TAG}" ]; then
    log "already on latest tag (${LATEST_TAG}), nothing to do"
    exit 0
fi

# Defensive: nur "vorwärts" updaten. Wenn LATEST älter wäre als CURRENT
# (z.B. Tag wurde gelöscht und re-tagged), kein automatisches Downgrade.
if [ -n "${CURRENT_TAG}" ]; then
    # `sort -V` macht semver-Vergleich. Wenn current zuletzt sortiert wird
    # → current ist neuer als latest → wir würden downgraden → abbrechen.
    NEWEST="$(printf '%s\n%s\n' "${CURRENT_TAG}" "${LATEST_TAG}" | sort -V | tail -1)"
    if [ "${NEWEST}" != "${LATEST_TAG}" ]; then
        log "current ${CURRENT_TAG} ist neuer als latest ${LATEST_TAG} — kein Downgrade, skip"
        exit 0
    fi
fi

# -----------------------------------------------------------------------------
# Idle-Check via API
log "checking orchestrator idle-state via ${API_BASE}/status"
STATUS_JSON="$(curl -fsS -m 5 "${API_BASE}/status" 2>/dev/null || echo '')"

if [ -z "${STATUS_JSON}" ]; then
    # Kein API-Response = Service down. Das ist ein Edge-Case: Pi läuft,
    # aber Backend tot. In dem Fall WOLLEN wir updaten (vermutlich pullt
    # man einen Fix), nicht skippen.
    log "no API response — controller appears dead, proceeding with update (might be fixing it)"
else
    STATE="$(printf '%s' "${STATUS_JSON}" | jq -r '.state // "UNKNOWN"')"
    QSO_CALL="$(printf '%s' "${STATUS_JSON}" | jq -r '.current_qso_call // ""')"
    PTT="$(printf '%s' "${STATUS_JSON}" | jq -r '.rig.ptt // false')"
    log "state=${STATE} current_qso_call=${QSO_CALL:-<none>} ptt=${PTT}"

    # Erlaubte Idle-States: IDLE, alles mit "WAIT" im Namen. Unsafe:
    # QSO_RESPOND, QSO_REPORT, QSO_LOG, TX_LOCKED, GRACE, CQ (sendet aktiv).
    case "${STATE}" in
        IDLE|"")
            : ;;
        *)
            log "not idle (state=${STATE}), skip — Timer feuert wieder in 10 min"
            ntfy "🟡" "Update ${CURRENT_TAG:-<untagged>} → ${LATEST_TAG} verfügbar, skip (state=${STATE})"
            exit 0
            ;;
    esac
    if [ -n "${QSO_CALL}" ] && [ "${QSO_CALL}" != "null" ]; then
        log "current QSO with ${QSO_CALL} active, skip"
        ntfy "🟡" "Update ${LATEST_TAG} verfügbar, skip (QSO mit ${QSO_CALL} läuft)"
        exit 0
    fi
    if [ "${PTT}" = "true" ]; then
        log "PTT active, skip"
        ntfy "🟡" "Update ${LATEST_TAG} verfügbar, skip (PTT on)"
        exit 0
    fi
fi

# -----------------------------------------------------------------------------
# Rollback-Target merken (wir checkout-en NICHT mit tag-detach in einem
# named branch, sondern speichern den Commit-Hash für späteres
# git checkout zurück).
ROLLBACK_REF="$(git rev-parse HEAD)"
log "rollback target: ${ROLLBACK_REF} (was ${CURRENT_DESC})"

# -----------------------------------------------------------------------------
# Pre-checkout: requirements.txt / pyproject Hash MERKEN. Wenn nach
# checkout der Hash unterschiedlich → pip install nötig. Spart Zeit
# wenn Releases nur Code-Änderungen ohne Deps sind.
PYPROJECT_HASH_BEFORE="$(sha256sum backend/pyproject.toml 2>/dev/null | awk '{print $1}' || echo '')"

# -----------------------------------------------------------------------------
T0="$(date +%s)"
log "checkout ${LATEST_TAG}"
if ! git checkout --quiet "${LATEST_TAG}"; then
    die "git checkout ${LATEST_TAG} failed — Pi bleibt auf ${CURRENT_DESC}"
fi

PYPROJECT_HASH_AFTER="$(sha256sum backend/pyproject.toml 2>/dev/null | awk '{print $1}' || echo '')"

# -----------------------------------------------------------------------------
# Python-Deps refreshen wenn pyproject sich geändert hat.
NEEDS_PIP=0
if [ "${PYPROJECT_HASH_BEFORE}" != "${PYPROJECT_HASH_AFTER}" ]; then
    NEEDS_PIP=1
    log "pyproject changed → pip install -e .[hardware]"
fi

# -----------------------------------------------------------------------------
# Inner function so we can also call it during rollback.
install_and_restart() {
    local label="$1"
    if [ "${NEEDS_PIP}" = "1" ]; then
        if ! (cd backend && .venv/bin/pip install --quiet -e '.[hardware]'); then
            log "pip install fehlgeschlagen (${label})"
            return 1
        fi
    fi
    # ft8_lib rebuild — Library wird zur Laufzeit per cffi geladen. Wenn
    # vendor/ft8_lib sich geändert hat, müssen wir die .so neu bauen.
    # Wir machen das defensiv immer (make ist no-op wenn nichts neu ist),
    # aber nur wenn vendor/ft8_lib/Makefile existiert.
    if [ -f vendor/ft8_lib/Makefile ]; then
        (cd vendor/ft8_lib && make --quiet CFLAGS='-O3 -DHAVE_STPCPY -I. -fPIC') \
            || { log "ft8_lib build failed (${label})"; return 1; }
    fi
    log "systemctl restart ft8-controller (${label})"
    if ! sudo -n /bin/systemctl restart ft8-controller; then
        log "systemctl restart fehlgeschlagen — sudoers-snippet fehlt?"
        return 1
    fi
    return 0
}

# -----------------------------------------------------------------------------
if ! install_and_restart "forward"; then
    log "forward install/restart fehlgeschlagen → Rollback"
    git checkout --quiet "${ROLLBACK_REF}" || die "Rollback-checkout selbst fehlgeschlagen"
    NEEDS_PIP=1  # bei rollback immer pip neu — sicherer
    if install_and_restart "rollback"; then
        ntfy "🟡" "Update ${LATEST_TAG} install/restart failed → rolled back zu ${CURRENT_DESC}"
        exit 0
    else
        die "Update UND Rollback failed — manueller Eingriff: ssh $(hostname) 'sudo systemctl status ft8-controller'"
    fi
fi

# -----------------------------------------------------------------------------
# Health-Probe nach Restart.
log "Health-Probe in ${HEALTH_WAIT_S}s …"
sleep "${HEALTH_WAIT_S}"

HEALTHY=0
for i in $(seq 1 "${HEALTH_RETRIES}"); do
    HEALTH_JSON="$(curl -fsS -m 5 "${API_BASE}/healthcheck" 2>/dev/null || echo '')"
    if [ -n "${HEALTH_JSON}" ]; then
        OVERALL="$(printf '%s' "${HEALTH_JSON}" | jq -r '.overall // "unknown"')"
        log "Health-Probe ${i}/${HEALTH_RETRIES}: overall=${OVERALL}"
        # green oder yellow akzeptieren wir. yellow ist oft "GPS-Fix
        # noch nicht da" — kein Grund für Rollback.
        if [ "${OVERALL}" = "green" ] || [ "${OVERALL}" = "yellow" ]; then
            HEALTHY=1
            break
        fi
    else
        log "Health-Probe ${i}/${HEALTH_RETRIES}: keine Antwort"
    fi
    sleep "${HEALTH_RETRY_DELAY_S}"
done

if [ "${HEALTHY}" != "1" ]; then
    log "Health-Probe failed nach Update → Rollback"
    git checkout --quiet "${ROLLBACK_REF}" || die "Rollback-checkout selbst fehlgeschlagen"
    NEEDS_PIP=1
    if install_and_restart "rollback"; then
        ntfy "🟡" "Update ${LATEST_TAG} health-check failed → rolled back zu ${CURRENT_DESC}"
        exit 0
    else
        die "Health-Check failed + Rollback failed — manueller Eingriff nötig"
    fi
fi

ELAPSED=$(( $(date +%s) - T0 ))
log "✓ Update ${CURRENT_DESC} → ${LATEST_TAG} erfolgreich (${ELAPSED}s)"
ntfy "🟢" "Update ${CURRENT_DESC} → ${LATEST_TAG} ok (${ELAPSED}s)"
exit 0
