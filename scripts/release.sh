#!/usr/bin/env bash
# release.sh — cut a new tagged release of the FT8-Appliance.
#
# Aufgerufen auf der Workstation, NICHT auf dem Pi. Macht:
#   1. Sanity-Checks (git clean, auf main, version-tag noch nicht
#      vergeben, version ist semver vMAJOR.MINOR.PATCH).
#   2. npm install + npm run build im frontend/
#   3. backend/ft8_appliance/web/static/ aus dem frischen Vite-Build
#      synchronisieren.
#   4. Falls etwas geändert wurde: commit "release: build for ${TAG}".
#   5. git tag ${TAG} + push.
#
# Die Pis (ft8 + ft8-2) holen sich den neuen Tag automatisch via
# ft8-self-update.timer innerhalb von ~10 Minuten (oder sofort via
# Konfig-UI Button).
#
# Usage:
#   ./scripts/release.sh v0.1.0
#   ./scripts/release.sh v0.1.0 --dry-run

set -euo pipefail

# -----------------------------------------------------------------------------
TAG="${1:-}"
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
    esac
done

if [ -z "${TAG}" ] || [ "${TAG}" = "--dry-run" ]; then
    echo "usage: $0 vMAJOR.MINOR.PATCH [--dry-run]" >&2
    exit 2
fi

# Semver-Format prüfen — vermeidet typos wie "0.1.0" (ohne v) oder "v0.1".
if [[ ! "${TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "FATAL: '${TAG}' ist kein semver-Tag (erwarte vMAJOR.MINOR.PATCH)" >&2
    exit 2
fi

# -----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO}"

step() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
abort() { printf '\033[1;31mFATAL: %s\033[0m\n' "$*" >&2; exit 1; }

run() {
    if [ "${DRY_RUN}" = "1" ]; then
        printf '  [dry-run] %s\n' "$*"
    else
        # Subshell — sonst persistiert ein „cd frontend" in den nächsten
        # Aufruf und der zweite cd findet den Pfad nicht mehr.
        ( eval "$@" )
    fi
}

# -----------------------------------------------------------------------------
step "Sanity-Checks"

# Auf main?
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "${BRANCH}" != "main" ]; then
    abort "nicht auf main (aktuell: ${BRANCH}) — release nur von main"
fi

# Clean working tree?
if [ -n "$(git status --porcelain)" ]; then
    abort "working tree nicht clean — committe / stash erst"
fi

# Tag noch frei?
if git rev-parse "${TAG}" >/dev/null 2>&1; then
    abort "Tag ${TAG} existiert schon — wähle einen anderen"
fi

# Upstream synced?
git fetch --quiet origin
LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_HEAD="$(git rev-parse origin/main 2>/dev/null || echo '')"
if [ -n "${REMOTE_HEAD}" ] && [ "${LOCAL_HEAD}" != "${REMOTE_HEAD}" ]; then
    abort "local main weicht von origin/main ab — rebase/push erst"
fi
echo "  ✓ on main, clean, tag frei, upstream sync"

# -----------------------------------------------------------------------------
step "Frontend bauen"

if [ ! -d "frontend" ]; then
    abort "kein frontend/-Verzeichnis"
fi

if ! command -v npm >/dev/null; then
    abort "npm nicht installiert — release läuft NUR auf der Workstation, nicht auf dem Pi"
fi

run "cd frontend && npm install --no-audit --no-fund"
# vite.config schreibt direkt nach ../backend/ft8_appliance/web/static/
# (emptyOutDir=true räumt vorher auf), also kein Copy-Step danach nötig.
run "cd frontend && npm run build"

STATIC_DIR="backend/ft8_appliance/web/static"
if [ "${DRY_RUN}" = "0" ]; then
    if [ ! -f "${STATIC_DIR}/index.html" ]; then
        abort "Build hat ${STATIC_DIR}/index.html nicht produziert"
    fi
    echo "  ✓ Build: $(find "${STATIC_DIR}" -type f | wc -l) Dateien in ${STATIC_DIR}/"
fi

# -----------------------------------------------------------------------------
step "VERSION-File aktualisieren"
VERSION_FILE="backend/ft8_appliance/_version.py"
if [ "${DRY_RUN}" = "0" ]; then
    cat > "${VERSION_FILE}" <<EOF
# Auto-generiert von scripts/release.sh — NICHT manuell editieren.
__version__ = "${TAG#v}"
__tag__ = "${TAG}"
EOF
fi
echo "  ✓ ${VERSION_FILE} → ${TAG}"

# -----------------------------------------------------------------------------
step "Changelog"

# Eintrag aus den Commit-Subjects seit dem letzten Tag bauen (HEAD ist hier
# noch der letzte Feature-Commit; der release-Build-Commit kommt danach).
LAST_TAG="$(git tag -l 'v*' --sort=-v:refname | head -1)"
ENTRY_FILE="$(mktemp)"
trap 'rm -f "${ENTRY_FILE}" "${ENTRY_FILE}.x"' EXIT
{
    echo "## ${TAG} — $(date +%F)"
    git log --no-merges --pretty='%s' "${LAST_TAG}..HEAD" 2>/dev/null \
        | grep -vE '^(release: build for|Auto-generiert|Merge |Co-Authored-By)' \
        | sed 's/^/- /'
    echo
} > "${ENTRY_FILE}"
echo "  Eintrag für ${TAG} (seit ${LAST_TAG:-Anfang}):"
sed 's/^/    /' "${ENTRY_FILE}"

if [ ! -f CHANGELOG.md ]; then
    ./scripts/gen_changelog.sh >/dev/null 2>&1 || true
fi
# Neuen Block nach dem Intro (vor dem ersten "## ") einfügen.
if [ -f CHANGELOG.md ] && grep -q '^## ' CHANGELOG.md; then
    awk -v ef="${ENTRY_FILE}" '
        !ins && /^## / { while ((getline l < ef) > 0) print l; ins=1 }
        { print }
    ' CHANGELOG.md > "${ENTRY_FILE}.x" && mv "${ENTRY_FILE}.x" CHANGELOG.md
else
    cat "${ENTRY_FILE}" >> CHANGELOG.md
fi
echo "  ✓ CHANGELOG.md aktualisiert"

# -----------------------------------------------------------------------------
step "Commit (wenn Änderungen)"

# Stage exakt die release-relevanten Pfade
run "git add ${STATIC_DIR} ${VERSION_FILE} CHANGELOG.md"

if [ "${DRY_RUN}" = "0" ] && [ -z "$(git diff --cached --name-only)" ]; then
    echo "  (keine Änderungen — Frontend-Build war identisch)"
else
    run "git commit -m 'release: build for ${TAG}' -m 'Auto-generiert von scripts/release.sh'"
    echo "  ✓ release-commit erstellt"
fi

# -----------------------------------------------------------------------------
step "Tag + Push"

# Annotation = der Changelog-Eintrag (echte Änderungen statt "Release X").
run "git tag -a ${TAG} -F ${ENTRY_FILE}"

if [ "${DRY_RUN}" = "0" ]; then
    echo
    echo "Tag ${TAG} lokal erstellt. Push? [y/N]"
    read -r CONFIRM
    if [ "${CONFIRM}" = "y" ] || [ "${CONFIRM}" = "Y" ]; then
        git push origin main
        git push origin "${TAG}"
        echo "  ✓ pushed origin/main + ${TAG}"
        # Optional: GitHub-Release mit Notes anlegen, falls gh installiert+authed.
        if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
            if gh release create "${TAG}" --title "${TAG}" --notes-file "${ENTRY_FILE}" >/dev/null 2>&1; then
                echo "  ✓ GitHub-Release ${TAG} erstellt"
            else
                echo "  (GitHub-Release ${TAG} nicht erstellt — evtl. existiert es schon)"
            fi
        else
            echo "  (gh nicht verfügbar/authed → kein GitHub-Release; Tag-Annotation reicht)"
        fi
    else
        echo "  → Tag bleibt lokal. Push später mit: git push origin main && git push origin ${TAG}"
    fi
else
    echo "  [dry-run] würde 'git push origin main' + 'git push origin ${TAG}' machen"
fi

echo
echo "------------------------------------------------------------"
echo "  Release ${TAG} fertig."
echo "  Pis holen sich's via Self-Update-Timer (max ~10min)"
echo "  oder sofort: Konfig-Seite → 'Jetzt updaten'."
echo "------------------------------------------------------------"
