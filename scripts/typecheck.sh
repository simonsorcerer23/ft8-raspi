#!/usr/bin/env bash
# Typ-Gate gegen die "Methode/Attribut/Name existiert gar nicht"-Bugklasse
# (z.B. ntfy.push() das nie existierte, vom Mock/try-except verschluckt).
#
# Strategie: BASELINE-RATCHET. Wir koennen den Altbestand an mypy-Findings
# nicht auf null bringen (viele sind reine Narrowing-Limits/False-Positives),
# aber wir verhindern NEUE Treffer der Crash-Bugklasse. Faellt also nur, wenn
# ein Commit einen neuen attr-defined/call-arg/arg-type/... einfuehrt.
#
# Aufruf: scripts/typecheck.sh           → prueft gegen Baseline
#         scripts/typecheck.sh --update  → schreibt Baseline neu (bewusst!)
set -euo pipefail

cd "$(dirname "$0")/../backend"
BASELINE="../scripts/mypy_baseline.txt"
MYPY=".venv/bin/python -m mypy"

# Fehlt mypy (frischer Checkout / Pi), Gate ueberspringen statt blockieren.
if ! .venv/bin/python -c "import mypy" 2>/dev/null; then
    echo "⚠ mypy nicht installiert — Typ-Gate übersprungen (.venv/bin/pip install mypy)"
    exit 0
fi

# Crash-relevante Error-Codes (NICHT Style wie type-arg/no-untyped-def).
CODES='attr-defined|call-arg|call-overload|arg-type|union-attr|name-defined|valid-type|operator|index|return-value|assignment|misc'

run_mypy() {
    # mypy exit!=0 ist NORMAL (es findet Fehler) — darf den Gate-Lauf nicht
    # abbrechen. Output in eine Var holen (|| true), dann filtern.
    # Zeilennummern strippen (s/:123: error:/: error:/) damit Edits oberhalb
    # die Baseline nicht churnen — Datei+Meldung+Code bleibt stabil.
    local out
    out="$($MYPY ft8_appliance --no-error-summary --ignore-missing-imports 2>&1 || true)"
    printf '%s\n' "$out" \
        | grep -E "\[($CODES)\]$" \
        | sed -E 's/:[0-9]+: error:/: error:/' \
        | sort -u || true
}

if [[ "${1:-}" == "--update" ]]; then
    run_mypy > "$BASELINE"
    echo "Baseline neu geschrieben: $(wc -l < "$BASELINE") Eintraege → $BASELINE"
    exit 0
fi

if [[ ! -f "$BASELINE" ]]; then
    echo "FEHLER: keine Baseline ($BASELINE). Einmalig: scripts/typecheck.sh --update" >&2
    exit 2
fi

CURRENT="$(mktemp)"
trap 'rm -f "$CURRENT"' EXIT
run_mypy > "$CURRENT"

# Neue Findings = in CURRENT, nicht in BASELINE.
NEW="$(comm -13 <(sort -u "$BASELINE") "$CURRENT" || true)"
if [[ -n "$NEW" ]]; then
    echo "✗ NEUE Typ-Findings der Crash-Bugklasse (nicht in Baseline):" >&2
    echo "$NEW" >&2
    echo "" >&2
    echo "Entweder echten Bug fixen, oder — falls bewusster False-Positive —" >&2
    echo "Baseline aktualisieren: scripts/typecheck.sh --update" >&2
    exit 1
fi
echo "✓ keine neuen Crash-Bugklasse-Findings (Baseline: $(wc -l < "$BASELINE"))"

# Zusaetzlich: ruff F-Codes (pyflakes) MUESSEN sauber sein — fangen undefinierte
# Namen (F821 = NameError-Crash), tote Imports/Vars (F401/F841), Redefinitionen.
# Kein Baseline-Ratchet noetig: F ist aktuell auf null und soll's bleiben.
if .venv/bin/python -c "import ruff" 2>/dev/null || .venv/bin/ruff --version >/dev/null 2>&1; then
    if ! .venv/bin/python -m ruff check ft8_appliance --select F --no-cache --quiet; then
        echo "✗ ruff F-Codes (undefinierte Namen / tote Imports) — bitte fixen" >&2
        exit 1
    fi
    echo "✓ ruff F-Codes sauber"
else
    echo "⚠ ruff nicht installiert — F-Code-Gate übersprungen"
fi
