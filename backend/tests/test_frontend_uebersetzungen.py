"""Beide Sprachen vollstaendig, kein Schluessel ohne Text.

Die Konvention verlangt fuer jeden Oberflaechen-String einen DE- und einen
EN-Eintrag. Geprueft hat das bisher niemand: Es gibt keine
Frontend-Testinfrastruktur, und im Backend sah keiner nach den Locales.
Am 2026-09-12 war der Stand sauber (512 Schluessel je Sprache, null
Abweichungen) — dieser Test haelt ihn so.

Ein fehlender Schluessel faellt im Betrieb nicht auf: Die Oberflaeche zeigt
dann den Schluesselnamen selbst an, was auf einem Dashboard voller
Abkuerzungen leicht uebersehen wird.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[2] / "frontend" / "src"
LOCALES = WURZEL / "lib" / "locales"

pytestmark = pytest.mark.skipif(
    not LOCALES.is_dir(), reason="Frontend-Quellen nicht vorhanden",
)


def _schluessel(pfad: Path) -> set[str]:
    txt = pfad.read_text(encoding="utf-8", errors="ignore")
    return set(re.findall(r"^\s*['\"]([a-zA-Z0-9_.]+)['\"]\s*:", txt, re.M))


def _verwendete() -> set[str]:
    benutzt: set[str] = set()
    for p in WURZEL.rglob("*"):
        if p.suffix in (".svelte", ".js") and "locales" not in str(p):
            txt = p.read_text(encoding="utf-8", errors="ignore")
            benutzt |= set(re.findall(r"\bt\(\s*['\"]([a-zA-Z0-9_.]+)['\"]", txt))
    return benutzt


def test_beide_sprachen_vorhanden():
    assert (LOCALES / "de.js").is_file()
    assert (LOCALES / "en.js").is_file()


def test_gleiche_schluessel_in_beiden_sprachen():
    de = _schluessel(LOCALES / "de.js")
    en = _schluessel(LOCALES / "en.js")
    nur_de = sorted(de - en)
    nur_en = sorted(en - de)
    assert not nur_de, f"ohne englische Entsprechung: {nur_de[:20]}"
    assert not nur_en, f"ohne deutsche Entsprechung: {nur_en[:20]}"


@pytest.mark.parametrize("sprache", ["de", "en"])
def test_jeder_verwendete_schluessel_existiert(sprache):
    """Sonst zeigt die Oberflaeche den Schluesselnamen statt eines Textes."""
    vorhanden = _schluessel(LOCALES / f"{sprache}.js")
    fehlend = sorted(k for k in _verwendete() if k not in vorhanden)
    assert not fehlend, f"fehlt in {sprache}.js: {fehlend[:20]}"


def test_es_gibt_ueberhaupt_schluessel():
    """Faengt den Fall ab, dass die Regex am Dateiformat vorbeiliest und
    der Test dadurch stumm alles durchwinkt."""
    assert len(_schluessel(LOCALES / "de.js")) > 200
    assert len(_verwendete()) > 200
