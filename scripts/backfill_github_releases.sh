#!/usr/bin/env bash
# Legt für JEDEN bestehenden git-Tag ein GitHub-Release mit Notes an
# (Notes = Commit-Subjects seit dem Vorgänger-Tag, via gen_changelog.sh).
# Einmalig laufen lassen, nachdem `gh` installiert + `gh auth login` gemacht
# wurde. Idempotent: existierende Releases werden übersprungen.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v gh >/dev/null 2>&1; then
    echo "FEHLER: 'gh' (GitHub CLI) ist nicht installiert." >&2
    echo "  Debian/Ubuntu: sudo apt install gh   — oder https://cli.github.com/" >&2
    exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
    echo "FEHLER: gh ist nicht authentifiziert. Einmalig: gh auth login" >&2
    exit 1
fi

mapfile -t tags < <(git tag -l 'v*' --sort=-v:refname)
n=${#tags[@]}
created=0; skipped=0
for ((i=0; i<n; i++)); do
    tag="${tags[$i]}"
    prev="${tags[$((i+1))]:-}"
    if gh release view "$tag" >/dev/null 2>&1; then
        skipped=$((skipped+1)); continue
    fi
    notes="$(./scripts/gen_changelog.sh --entry "$tag" "$prev")"
    if gh release create "$tag" --title "$tag" --notes "$notes" >/dev/null 2>&1; then
        echo "  ✓ Release $tag erstellt"
        created=$((created+1))
    else
        echo "  ! Release $tag fehlgeschlagen" >&2
    fi
done
echo "Fertig: $created erstellt, $skipped übersprungen (existierten schon)."
