#!/usr/bin/env bash
# Generiert CHANGELOG.md aus den annotierten git-Tags + den Commit-Subjects
# dazwischen. Idempotent: einfach neu laufen lassen, schreibt CHANGELOG.md.
#
#   scripts/gen_changelog.sh            # volle Regeneration aus allen Tags
#   scripts/gen_changelog.sh --entry vX.Y.Z <prevtag>   # nur den Block für
#                                          eine Version auf stdout (release.sh)
set -euo pipefail
cd "$(dirname "$0")/.."

# Subjects zwischen zwei Refs, Auto-/Noise-Zeilen raus.
emit_subjects() {
    local range="$1"
    git log --no-merges --pretty='%s' $range 2>/dev/null \
        | grep -vE '^(release: build for|Auto-generiert|Merge |Co-Authored-By)' \
        | sed 's/^/- /'
}

# Einzelblock für eine Version (genutzt von release.sh zum Voranstellen).
if [[ "${1:-}" == "--entry" ]]; then
    tag="$2"; prev="${3:-}"
    date="$(git log -1 --format=%ad --date=short "$tag" 2>/dev/null || date +%F)"
    echo "## ${tag} — ${date}"
    if [[ -n "$prev" ]]; then emit_subjects "${prev}..${tag}"; else emit_subjects "$tag"; fi
    echo
    exit 0
fi

# Volle Regeneration ------------------------------------------------------
{
    echo "# Changelog"
    echo
    echo "Alle nennenswerten Änderungen dieses Projekts. Generiert aus den"
    echo "git-Tags via \`scripts/gen_changelog.sh\` (Quelle: Commit-Messages)."
    echo
    mapfile -t tags < <(git tag -l 'v*' --sort=-v:refname)
    n=${#tags[@]}
    for ((i=0; i<n; i++)); do
        tag="${tags[$i]}"
        prev="${tags[$((i+1))]:-}"
        date="$(git log -1 --format=%ad --date=short "$tag" 2>/dev/null || echo '?')"
        echo "## ${tag} — ${date}"
        if [[ -n "$prev" ]]; then emit_subjects "${prev}..${tag}"; else emit_subjects "$tag"; fi
        echo
    done
} > CHANGELOG.md

echo "CHANGELOG.md geschrieben ($(grep -c '^## ' CHANGELOG.md) Versionen)."
