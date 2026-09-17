"""Wer uns wann hoert — das Tagesprofil der Empfangsberichte.

Die Zahl auf der Stationsseite soll eine Aussage ueber die Ausbreitung
sein. Drei Fehler wuerden sie still verfaelschen: Berichte statt Hoerer
zaehlen, ueber Stunden mitteln, in denen gar nicht gesendet wurde, und
Fehlzuordnungen auf fremden Baendern als Bandnutzung zeigen.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ft8_appliance.analyse.hoerprofil import baue_profil
from ft8_appliance.db import models as m
from ft8_appliance.db.session import init_engine, session_scope
from ft8_appliance.integrations import CtyDat
from ft8_appliance.web.routes import integrations as routen

KONT = {"DL": "EU", "JA": "AS", "W": "NA"}
LAND = {"DL": ("Germany", "🇩🇪"), "JA": ("Japan", "🇯🇵"), "W": ("United States", "🇺🇸")}


def _praefix(call: str) -> str:
    return next(p for p in ("DL", "JA", "W") if call.startswith(p))


def _profil(zeilen, **kw):
    return baue_profil(zeilen, lambda c: KONT[_praefix(c)],
                       lambda c: LAND[_praefix(c)], **kw)


def test_gezaehlt_werden_hoerer_nicht_berichte() -> None:
    """DL1AA meldet uns in einer Stunde zwanzigmal — das ist ein Hoerer."""
    zeilen = [("20m", "2026-09-16", 8, "DL1AA")] * 20 + \
             [("20m", "2026-09-16", 8, "JA1BB")]
    p = _profil(zeilen, mindest_empfaenger=1)
    h8 = p[0]["stunden"][8]
    assert h8["gesamt"] == 2
    assert h8["kontinente"] == {"EU": 1, "AS": 1}


def test_mittel_nur_ueber_tage_mit_berichten() -> None:
    """Um 3 Uhr kamen nur an einem von zwei Tagen Berichte — vermutlich hat
    die Station da nicht gesendet. Das darf den Schnitt nicht halbieren."""
    zeilen = [
        ("20m", "2026-09-15", 8, "DL1AA"), ("20m", "2026-09-15", 8, "DL2AA"),
        ("20m", "2026-09-16", 8, "DL1AA"), ("20m", "2026-09-16", 8, "DL3AA"),
        ("20m", "2026-09-16", 3, "W1AA"), ("20m", "2026-09-16", 3, "W2AA"),
    ]
    p = _profil(zeilen, mindest_empfaenger=1)[0]["stunden"]
    assert p[8] == {**p[8], "tage": 2, "gesamt": 2.0}
    assert p[3]["tage"] == 1 and p[3]["gesamt"] == 2.0
    assert p[5]["tage"] == 0 and p[5]["gesamt"] == 0


def test_geisterbaender_fallen_raus_echte_bleiben() -> None:
    """Ein einzelner Bericht auf 17 m ist eine Fehlzuordnung, keine
    Bandnutzung. Mit der neuen Antenne kommen echte Baender dazu — die
    muessen dann ohne weiteres Zutun erscheinen."""
    zeilen = [("20m", "2026-09-16", 8, f"DL{i}AA") for i in range(40)]
    zeilen += [("17m", "2026-09-16", 9, "JA1BB")]
    assert [b["band"] for b in _profil(zeilen)] == ["20m"]

    zeilen += [("40m", "2026-09-16", 20, f"W{i}AA") for i in range(35)]
    assert [b["band"] for b in _profil(zeilen)] == ["40m", "20m"], \
        "Baender in Frequenzfolge, 40 m vor 20 m"


def test_top_laender_und_unbekannte_rufzeichen() -> None:
    zeilen = [("20m", "2026-09-16", 8, c) for c in
              ("DL1AA", "DL2AA", "DL3AA", "JA1BB", "W1CC", "W2CC", "ZZ9ZZ")]
    p = baue_profil(
        zeilen,
        lambda c: KONT.get(c[:2]) or KONT.get(c[:1]),
        lambda c: LAND.get(c[:2]) or LAND.get(c[:1]),
        mindest_empfaenger=1,
    )[0]["stunden"][8]
    assert p["top"][0] == ["🇩🇪", "Germany"]
    assert p["top"][1] == ["🇺🇸", "United States"]
    assert p["laender"] == 3
    # ZZ9ZZ zaehlt als Hoerer, landet aber in keinem Kontinent
    assert p["gesamt"] == 7
    assert sum(p["kontinente"].values()) == 6


CTY = """Fed. Rep. of Germany:     14:  28:  EU:   51.00:   -10.00:    -1.0:  DL:
    DA,DB,DC,DD,DE,DF,DG,DH,DI,DJ,DK,DL,DM,DN,DO,DP,DQ,DR;
Japan:                    25:  45:  AS:   36.40:  -138.38:    -9.0:  JA:
    JA,JE,JF,JG,JH,JI,JJ,JK,JL,JM,JN,JO,JP,JQ,JR,JS;
"""


@pytest.mark.asyncio
async def test_endpunkt_liest_die_datenbank_und_gibt_keine_rufzeichen_heraus() -> None:
    """Ende zu Ende: das Datumsformat in SQLite, die Stundenzerlegung und
    die Zusage an die Webseite, dass kein fremdes Rufzeichen hinausgeht."""
    eng = init_engine(None)
    async with eng.begin() as c:
        await c.run_sync(m.Base.metadata.create_all)
    jetzt = datetime.now(UTC).replace(minute=10, second=0, microsecond=0)
    async with session_scope() as s:
        for i in range(35):
            s.add(m.PskReporterIn(ts=jetzt - timedelta(minutes=i % 5), rx_call=f"DL{i}XYZ",
                                  rx_grid="JO40", snr_db=-10, band="20m"))
        s.add(m.PskReporterIn(ts=jetzt, rx_call="JA1ABC", rx_grid="PM95",
                              snr_db=-18, band="20m"))
        # ausserhalb des Fensters
        s.add(m.PskReporterIn(ts=jetzt - timedelta(days=9), rx_call="JA9OLD",
                              rx_grid="PM95", snr_db=-18, band="20m"))

    routen._PROFIL_CACHE.clear()
    orch = SimpleNamespace(integrations=SimpleNamespace(cty=CtyDat.from_string(CTY)))
    antwort = await routen.psk_tagesprofil(tage=7, orch=orch)

    assert [b["band"] for b in antwort["baender"]] == ["20m"]
    stunde = antwort["baender"][0]["stunden"][jetzt.hour]
    assert stunde["gesamt"] == 36
    assert stunde["kontinente"] == {"EU": 35, "AS": 1}
    assert antwort["baender"][0]["empfaenger"] == 36, "JA9OLD liegt ausserhalb"
    roh = repr(antwort)
    for call in ("DL0XYZ", "JA1ABC", "JA9OLD"):
        assert call not in roh, f"{call} steht in der Antwort"
