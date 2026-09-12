"""Bilder hinter der Token-Pruefung duerfen nicht direkt als Quelle dienen.

Die Oberfläche authentifiziert mit einem ``Authorization``-Header. Ein
``<img src="/api/...">`` kann keinen Header setzen und bekommt 401 — das
Bild bleibt leer, ohne Fehlermeldung irgendwo. Genau so ist die erste
Fassung der MUF-Ebene ausgeliefert worden: Endpunkte geprüft, Lage
geprüft, Tests grün, und in der Oberfläche war nichts zu sehen.

Der Test hält die Regel fest, weil sie beim nächsten Bild wieder gilt.
"""
from __future__ import annotations

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def test_kein_api_pfad_als_bildquelle() -> None:
    """imageOverlay/img/background-image duerfen kein /api/... direkt laden."""
    verdaechtig: list[str] = []
    muster = (
        re.compile(r"imageOverlay\(\s*[`'\"]\s*/api/"),
        re.compile(r"<img[^>]+src=[\"'{`]\s*/api/"),
        re.compile(r"url\(\s*['\"]?/api/"),
    )
    for datei in FRONTEND.rglob("*.svelte"):
        text = datei.read_text()
        for m in muster:
            for treffer in m.finditer(text):
                zeile = text[: treffer.start()].count("\n") + 1
                verdaechtig.append(f"{datei.name}:{zeile}  {treffer.group(0)!r}")
    assert not verdaechtig, (
        "Bild-Quelle zeigt direkt auf /api/ — ohne Authorization-Header gibt das "
        "401 und bleibt still leer. Stattdessen api.blobUrl() benutzen:\n  "
        + "\n  ".join(verdaechtig)
    )


def test_blob_url_gibt_den_token_mit() -> None:
    """api.blobUrl muss den Header setzen, sonst ist der Umweg sinnlos."""
    api = (FRONTEND / "lib" / "api.js").read_text()
    i = api.index("async function blobUrl(")
    koerper = api[i : api.index("\n}", i)]
    assert "Authorization" in koerper and "Bearer" in koerper, \
        "blobUrl sendet keinen Authorization-Header"
    assert "createObjectURL" in koerper, "blobUrl liefert keine blob:-Adresse"


def test_blob_adresse_wird_freigegeben() -> None:
    """Ohne revokeObjectURL sammelt jede Aktualisierung Speicher an."""
    karte = (FRONTEND / "components" / "Map.svelte").read_text()
    assert "revokeObjectURL" in karte, "blob:-Adresse wird nie freigegeben"
