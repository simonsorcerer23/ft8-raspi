"""MUF-Weltkarte als Kartenebene: Abruf, Zwischenspeicher, Ausfallverhalten.

Der Dienst wird kostenlos betrieben. Die Tests halten deshalb vor allem
fest, wie selten wir ihn fragen — und dass ein Ausfall dort nicht in
Dauerabrufe von hier umschlaegt.
"""
from __future__ import annotations

import base64
import struct
import time
import zlib

import pytest

from ft8_appliance.integrations import muf_map
from ft8_appliance.integrations.muf_map import MufKartenCache, _png_aus_svg, _png_masse


def _png(breite: int, hoehe: int) -> bytes:
    """Ein gueltiges Mini-PNG der gewuenschten Masse."""
    def chunk(typ: bytes, daten: bytes) -> bytes:
        return (struct.pack(">I", len(daten)) + typ + daten
                + struct.pack(">I", zlib.crc32(typ + daten) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", breite, hoehe, 8, 2, 0, 0, 0)
    roh = b"".join(b"\x00" + b"\x00\x00\x00" * breite for _ in range(hoehe))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(roh)) + chunk(b"IEND", b""))


def _svg_mit(png: bytes) -> bytes:
    b64 = base64.b64encode(png).decode()
    return (b'<svg xmlns="http://www.w3.org/2000/svg"><g><image '
            + f'xlink:href="data:image/png;base64,{b64}"'.encode()
            + b' width="10" height="5"/></g></svg>')


# ------------------------------------------------------------------ Zerlegen

def test_png_wird_aus_dem_svg_geloest() -> None:
    png = _png(8, 4)
    assert _png_aus_svg(_svg_mit(png)) == png


def test_leerzeichen_und_umbruch_im_datenblock() -> None:
    """Der echte Renderer setzt ein Leerzeichen hinter ``base64,`` und darf
    umbrechen. Der erste Entwurf verlangte dort direkt Daten und fand
    nichts — gegen den echten Dienst, waehrend alle Tests gruen waren."""
    png = _png(8, 4)
    b64 = base64.b64encode(png).decode()
    umgebrochen = "\n".join(b64[i:i + 76] for i in range(0, len(b64), 76))
    for variante in (f" {b64}", f"\n  {umgebrochen}\n  "):
        svg = ('<svg><image xlink:href="data:image/png;base64,'
               + variante + '"/></svg>').encode()
        assert _png_aus_svg(svg) == png, f"scheitert an {variante[:3]!r}"


def test_svg_ohne_bild_liefert_nichts() -> None:
    assert _png_aus_svg(b"<svg><path d='M0 0'/></svg>") is None


def test_masse_kommen_aus_dem_png_kopf() -> None:
    assert _png_masse(_png(2732, 1366)) == (2732, 1366)


def test_fremde_daten_sind_kein_png() -> None:
    assert _png_masse(b"GIF89a" + b"\x00" * 40) is None


# -------------------------------------------------------------------- Abruf

class _Zaehler:
    """Ersetzt den Netzabruf und zaehlt, wie oft gefragt wurde."""

    def __init__(self, antwort: bytes | None) -> None:
        self.antwort = antwort
        self.anfragen = 0

    def __call__(self, *_a, **_k):
        self.anfragen += 1
        if self.antwort is None:
            raise OSError("Netz weg")
        class _R:
            def __init__(s, d): s._d = d
            def read(s, n=None): return s._d[:n] if n else s._d
            def __enter__(s): return s
            def __exit__(s, *a): return False
        return _R(self.antwort)


@pytest.fixture
def cache(tmp_path):
    return MufKartenCache(tmp_path / "muf.png")


def test_erster_abruf_holt_und_legt_ab(cache, tmp_path, monkeypatch) -> None:
    z = _Zaehler(_svg_mit(_png(2732, 1366)))
    monkeypatch.setattr(muf_map.urllib.request, "urlopen", z)
    karte = cache.hole()
    assert karte is not None and karte.breite == 2732
    assert (tmp_path / "muf.png").exists(), "Karte ueberlebt keinen Neustart"
    assert z.anfragen == 1


def test_zweiter_abruf_fragt_nicht_erneut(cache, monkeypatch) -> None:
    """Die Vorhersage wird stuendlich gerechnet — oefter zu fragen belastet
    den Dienst und liefert dieselben Zahlen."""
    z = _Zaehler(_svg_mit(_png(2732, 1366)))
    monkeypatch.setattr(muf_map.urllib.request, "urlopen", z)
    cache.hole()
    cache.hole()
    cache.hole()
    assert z.anfragen == 1


def test_nach_ablauf_wird_neu_geholt(cache, monkeypatch) -> None:
    z = _Zaehler(_svg_mit(_png(2732, 1366)))
    monkeypatch.setattr(muf_map.urllib.request, "urlopen", z)
    erste = cache.hole()
    assert erste is not None
    object.__setattr__(erste, "geholt_at", time.time() - muf_map._TTL_S - 1)
    cache.hole()
    assert z.anfragen == 2


def test_ausfall_liefert_die_alte_karte(cache, monkeypatch) -> None:
    gut = _Zaehler(_svg_mit(_png(2732, 1366)))
    monkeypatch.setattr(muf_map.urllib.request, "urlopen", gut)
    alt = cache.hole()
    assert alt is not None
    object.__setattr__(alt, "geholt_at", time.time() - muf_map._TTL_S - 1)

    kaputt = _Zaehler(None)
    monkeypatch.setattr(muf_map.urllib.request, "urlopen", kaputt)
    weiter = cache.hole()
    assert weiter is not None, "bei Ausfall darf die Karte nicht verschwinden"
    assert weiter.png == alt.png


def test_ausfall_loest_keine_dauerabrufe_aus(cache, monkeypatch) -> None:
    """Ohne Sperrfrist wuerde jeder Kartenaufruf waehrend einer Stoerung
    einen neuen Versuch ausloesen."""
    kaputt = _Zaehler(None)
    monkeypatch.setattr(muf_map.urllib.request, "urlopen", kaputt)
    for _ in range(5):
        assert cache.hole() is None
    assert kaputt.anfragen == 1, f"{kaputt.anfragen} Versuche statt einem"


def test_falsches_seitenverhaeltnis_wird_verworfen(cache, monkeypatch) -> None:
    """Ohne 2:1 ist es keine gleichabstaendige Weltkarte — die Bildebene
    laege falsch ueber der Karte, und das faellt niemandem auf."""
    z = _Zaehler(_svg_mit(_png(1000, 1000)))
    monkeypatch.setattr(muf_map.urllib.request, "urlopen", z)
    assert cache.hole() is None


def test_uebergrosse_antwort_wird_verworfen(cache, monkeypatch) -> None:
    z = _Zaehler(b"x" * (muf_map._MAX_BYTES + 10))
    monkeypatch.setattr(muf_map.urllib.request, "urlopen", z)
    assert cache.hole() is None


def test_karte_von_platte_ueberlebt_neustart(tmp_path, monkeypatch) -> None:
    z = _Zaehler(_svg_mit(_png(2732, 1366)))
    monkeypatch.setattr(muf_map.urllib.request, "urlopen", z)
    MufKartenCache(tmp_path / "muf.png").hole()

    # frischer Cache, wie nach einem Neustart: Datei ist da, Netz ist weg
    monkeypatch.setattr(muf_map.urllib.request, "urlopen", _Zaehler(None))
    neu = MufKartenCache(tmp_path / "muf.png")
    karte = neu.hole()
    assert karte is not None and karte.breite == 2732


# ------------------------------------------------------------------ Endpunkte

def test_endpunkte_liefern_bild_und_stand(tmp_path, monkeypatch) -> None:
    """Das Bild kommt als PNG, der Stand sagt wie alt es ist.

    Ohne den Stand waere eine veraltete Karte von einer aktuellen nicht zu
    unterscheiden — sie sieht ja genauso aus.
    """
    from fastapi.testclient import TestClient

    from ft8_appliance.web.routes import propagation

    monkeypatch.setattr(propagation, "_cache", MufKartenCache(tmp_path / "m.png"))
    monkeypatch.setattr(muf_map.urllib.request, "urlopen",
                        _Zaehler(_svg_mit(_png(2732, 1366))))

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(propagation.router, prefix="/api")
    c = TestClient(app)

    r = c.get("/api/propagation/muf-map")
    assert r.status_code == 200
    d = r.json()
    assert d["verfuegbar"] is True
    assert d["breite"] == 2732 and d["hoehe"] == 1366
    assert d["veraltet"] is False
    assert d["stand"] and d["alter_s"] is not None

    b = c.get("/api/propagation/muf-map.png")
    assert b.status_code == 200
    assert b.headers["content-type"] == "image/png"
    assert b.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_ohne_karte_meldet_der_endpunkt_das_ehrlich(tmp_path, monkeypatch) -> None:
    """503 statt 404: Die Ebene gibt es, nur gerade kein Bild."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ft8_appliance.web.routes import propagation

    monkeypatch.setattr(propagation, "_cache", MufKartenCache(tmp_path / "leer.png"))
    monkeypatch.setattr(muf_map.urllib.request, "urlopen", _Zaehler(None))

    app = FastAPI()
    app.include_router(propagation.router, prefix="/api")
    c = TestClient(app)

    assert c.get("/api/propagation/muf-map").json()["verfuegbar"] is False
    assert c.get("/api/propagation/muf-map.png").status_code == 503
