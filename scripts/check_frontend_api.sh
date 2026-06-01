#!/usr/bin/env bash
# Frontend-Gate gegen die "rohes fetch()/EventSource umgeht den Auth-Layer"-
# Bugklasse. Symptom: nach Einfuehrung der Token-Auth (v0.37) lieferten
# Panels mit rohem fetch() still 401 → leere Anzeige (Live-Konversation,
# Solar, Map-Coverage). Mocks/try-catch verschluckten den Fehler.
#
# Regel: JEDER Backend-Zugriff laeuft ueber den zentralen Layer, der Token
# + 401-Handling anhaengt:
#   - HTTP   → api.get()/request() in src/lib/api.js
#   - SSE    → _sseUrl() (haengt ?token= an) in src/lib/sound.svelte.js
# Ausserhalb dieser zwei Dateien ist rohes fetch()/new EventSource verboten.
set -euo pipefail

cd "$(dirname "$0")/../frontend/src"

# Erlaubte Dateien (die DEN Layer bilden):
ALLOW_FETCH="lib/api.js"
ALLOW_SSE="lib/sound.svelte.js"

fail=0

bad_fetch="$(grep -rn 'fetch(' . --include='*.svelte' --include='*.js' \
    | grep -v "^\./${ALLOW_FETCH}:" || true)"
if [[ -n "$bad_fetch" ]]; then
    echo "✗ rohes fetch() ausserhalb lib/api.js (umgeht Token-Auth → 401):" >&2
    echo "$bad_fetch" >&2
    fail=1
fi

bad_sse="$(grep -rn 'new EventSource' . --include='*.svelte' --include='*.js' \
    | grep -v "^\./${ALLOW_SSE}:" || true)"
if [[ -n "$bad_sse" ]]; then
    echo "✗ rohes new EventSource ausserhalb lib/sound.svelte.js (kein ?token= → 401):" >&2
    echo "$bad_sse" >&2
    fail=1
fi

if [[ "$fail" -ne 0 ]]; then
    echo "" >&2
    echo "→ Backend-Zugriff ueber api.get()/request() bzw. den SSE-Helper leiten." >&2
    exit 1
fi
echo "✓ Frontend: kein roher fetch()/EventSource ausserhalb des Auth-Layers"

# i18n-Guard: t('…') benutzen aber i18n.svelte.js NICHT importieren = Laufzeit-
# Crash (ReferenceError, vom Build NICHT gefangen). Genau so kam DecodeList
# kaputt raus (Migrations-Skript-Anker verfehlt).
bad_i18n=""
for f in $(grep -rlE "\bt\('" . --include='*.svelte'); do
    grep -q "i18n.svelte.js" "$f" || bad_i18n="$bad_i18n $f"
done
if [[ -n "$bad_i18n" ]]; then
    echo "✗ t('…') ohne i18n-Import (Laufzeit-Crash):" >&2
    for f in $bad_i18n; do echo "    $f" >&2; done
    exit 1
fi
echo "✓ Frontend: jede t()-Nutzung importiert i18n"
