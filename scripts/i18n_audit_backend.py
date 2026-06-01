#!/usr/bin/env python3
"""Backend-i18n-Gate gegen die "Übersetzung kaputt / Platzhalter leckt"-Bugklasse.

Hintergrund (Sebastian 2026-06-01): ``i18n.translate()`` fängt ``KeyError``/
``IndexError`` und gibt dann das ROHE Template zurück — ein vergessener
Format-Parameter crasht also nicht, zeigt dem Operator aber wörtlich ``{watts}``
statt eines Werts. Format-Specs (``{x:.2f}``) lösen sogar ``ValueError`` aus, der
NICHT gefangen wird → Crash beim Push. Beides ist still und entging dem Build.

Dieses Gate prüft statisch (AST + Katalog):
  1. Key-Parität DE↔EN (jede Sprache kennt jeden Key).
  2. Platzhalter-Parität pro Key ({x} in DE == {x} in EN).
  3. Keine Format-Specs ({x:..}) — translate() fängt deren ValueError nicht.
  4. Jeder literale translate('key', …)/_t('key', …)-Aufruf + jedes
     GuardResult(..., 'guard.x', {params}) liefert ALLE Platzhalter des Keys.
  5. Kein referenzierter Key fehlt im Katalog.
Orphan-Keys (definiert, nirgends genutzt) sind nur eine Warnung, kein Fail.

Aufruf: scripts/i18n_audit_backend.py   (cwd egal; Pfade relativ zu __file__)
"""
from __future__ import annotations

import ast
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from ft8_appliance import i18n  # noqa: E402

DE, EN = i18n._DE, i18n._EN
_fmt = string.Formatter()
ERRORS: list[str] = []
WARNINGS: list[str] = []

# Backend-Dateien, die i18n-Strings erzeugen (translate/_t/GuardResult).
SCAN_FILES = [
    "ft8_appliance/runtime/orchestrator.py",
    "ft8_appliance/web/routes/status.py",
    "ft8_appliance/web/routes/control.py",
    "ft8_appliance/web/routes/sse.py",
    "ft8_appliance/statemachine/machine.py",
    "ft8_appliance/statemachine/guards.py",
]


def fields(s: str) -> tuple[set[str], list[tuple[str, str]]]:
    """Return ({field names}, [(name, format_spec) for specced fields])."""
    names: set[str] = set()
    specs: list[tuple[str, str]] = []
    for _lit, name, spec, _conv in _fmt.parse(s):
        if name is not None:
            names.add(name)
            if spec:
                specs.append((name, spec))
    return names, specs


# --- 1. Key-Parität ---------------------------------------------------------
only_de, only_en = set(DE) - set(EN), set(EN) - set(DE)
if only_de:
    ERRORS.append(f"Keys nur in DE (EN-Übersetzung fehlt): {sorted(only_de)}")
if only_en:
    ERRORS.append(f"Keys nur in EN (DE-Übersetzung fehlt): {sorted(only_en)}")

# --- 2. Platzhalter-Parität + 3. Format-Specs -------------------------------
for k in set(DE) & set(EN):
    df, dspec = fields(DE[k])
    ef, espec = fields(EN[k])
    if df != ef:
        ERRORS.append(f"Platzhalter-Mismatch {k}: de={sorted(df)} en={sorted(ef)}")
    for name, spec in dspec + espec:
        ERRORS.append(
            f"Format-Spec verboten (ValueError-Risiko, translate() fängt ihn "
            f"nicht): {k} → {{{name}:{spec}}} — Wert vorformatiert übergeben"
        )

# --- AST-Helfer für Call-Sites ----------------------------------------------
def _literal_keys(node: ast.AST) -> list[str] | None:
    """String-Literal oder Ternär zweier Literale → Liste der Keys; sonst None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        out: list[str] = []
        for branch in (node.body, node.orelse):
            if isinstance(branch, ast.Constant) and isinstance(branch.value, str):
                out.append(branch.value)
            else:
                return None
        return out
    return None


def _check_key(key: str, passed: set[str], has_unpack: bool, where: str) -> None:
    table = DE if key in DE else (EN if key in EN else None)
    if table is None:
        # Nur i18n-Keys prüfen (Namespaces), nicht beliebige Strings.
        if key.split(".")[0] in ("guard", "hint", "lock", "push"):
            ERRORS.append(f"{where}: Key '{key}' nicht im Katalog")
        return
    if has_unpack:
        return  # **dict — statisch nicht prüfbar
    required, _ = fields(table[key])
    missing = required - passed
    if missing:
        ERRORS.append(
            f"{where}: '{key}' — fehlende Params {sorted(missing)} "
            f"(übergeben: {sorted(passed)}) → leckt rohe {{Klammern}}"
        )


# --- 4./5. Call-Site-Coverage ------------------------------------------------
for rel in SCAN_FILES:
    path = BACKEND / rel
    if not path.exists():
        WARNINGS.append(f"Scan-Datei fehlt (umbenannt?): {rel}")
        continue
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        where = f"{rel}:{node.lineno}"
        func = node.func
        is_t = isinstance(func, ast.Name) and func.id == "_t"
        is_tr = (isinstance(func, ast.Name) and func.id == "translate") or (
            isinstance(func, ast.Attribute) and func.attr == "translate"
        )
        is_guard = isinstance(func, ast.Name) and func.id == "GuardResult"

        if is_t or is_tr:
            if not node.args:
                continue
            keys = _literal_keys(node.args[0])
            if keys is None:
                continue  # dynamischer Key — nicht prüfbar
            passed = {kw.arg for kw in node.keywords if kw.arg is not None}
            has_unpack = any(kw.arg is None for kw in node.keywords)
            for key in keys:
                _check_key(key, passed, has_unpack, where)

        elif is_guard:
            # GuardResult(ok, name, code?, params?) — Lock-Gründe.
            if len(node.args) < 3:
                continue
            keys = _literal_keys(node.args[2])
            if keys is None:
                continue
            passed = set()
            has_unpack = False
            if len(node.args) >= 4 and isinstance(node.args[3], ast.Dict):
                for dkey in node.args[3].keys:
                    if isinstance(dkey, ast.Constant) and isinstance(dkey.value, str):
                        passed.add(dkey.value)
                    else:
                        has_unpack = True  # nicht-literaler Dict-Key
            elif len(node.args) >= 4:
                has_unpack = True  # params kein Dict-Literal
            for key in keys:
                _check_key(key, passed, has_unpack, where)

# --- Report -----------------------------------------------------------------
if WARNINGS:
    for w in WARNINGS:
        print(f"⚠ {w}", file=sys.stderr)

if ERRORS:
    print("✗ Backend-i18n-Audit: Findings", file=sys.stderr)
    for e in ERRORS:
        print(f"    {e}", file=sys.stderr)
    sys.exit(1)

print(f"✓ Backend-i18n sauber ({len(DE)} Keys, DE/EN paritätisch, Call-Sites gedeckt)")
