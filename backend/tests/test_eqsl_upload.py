"""eQSL-Upload: ADIF-Aufbau und Auswertung der Antwort.

Die Schnittstelle ist HTML-basiert und meldet nur Zahlen, keine
Einzelschicksale. Was hier geprueft wird, ist deshalb vor allem: dass wir
aus dieser duennen Antwort die richtigen Schluesse ziehen.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ft8_appliance.db.models import Qso
from ft8_appliance.integrations.eqsl import (
    EqslError, EqslErgebnis, baue_adif, lies_antwort, upload,
)

ANTWORT_KOPF = "<html><!-- Reply form eQSL.cc ADIF Real-time Interface -->"


def _qso(call: str = "OH3OJ", **kw) -> Qso:
    felder = dict(
        call=call, band="20m", mode="FT8", rst_sent="-12", rst_rcvd="-08",
        qso_start=datetime(2026, 9, 14, 8, 23, 45, tzinfo=UTC),
        user_callsign="DK9XR", station_callsign="DK9XR",
    )
    felder.update(kw)
    return Qso(**felder)


# ------------------------------------------------------------------ ADIF

def test_adif_traegt_die_pflichtfelder() -> None:
    t = baue_adif([_qso()])
    for erwartet in ("<call:5>OH3OJ", "<qso_date:8>20260914", "<time_on:4>0823",
                     "<band:3>20m", "<mode:3>FT8", "<rst_sent:3>-12"):
        assert erwartet in t, erwartet


def test_jeder_datensatz_endet_mit_eor() -> None:
    """Ohne <EOR> bricht eQSL den Import der ganzen Datei ab, ohne ein
    Wort zu sagen. Das ist der teuerste stille Fehler der Schnittstelle."""
    t = baue_adif([_qso("AA1AA"), _qso("BB2BB"), _qso("CC3CC")])
    assert t.lower().count("<eor>") == 3


def test_header_mit_eoh() -> None:
    t = baue_adif([_qso()])
    assert "<eoh>" in t.lower() and t.lower().index("<eoh>") < t.index("<call:")


@pytest.mark.parametrize("leer", [None, "", "   "])
def test_leere_felder_fallen_weg(leer) -> None:
    """Ein Feld mit Laenge 0 wuerde die Laengenangabe verfaelschen — und
    der Leerstring kommt aus der Datenbank haeufiger als None."""
    t = baue_adif([_qso(rst_sent=leer, rst_rcvd=leer)])
    assert "rst_sent" not in t and "rst_rcvd" not in t
    assert "<call:5>OH3OJ" in t
    assert ":0>" not in t


def test_laengenangaben_stimmen() -> None:
    """Die Laenge im Tag muss exakt der Zeichenzahl entsprechen, sonst
    verschiebt sich der Parser der Gegenseite."""
    import re
    t = baue_adif([_qso("SM6/DL1HTW")])
    for name, laenge, rest in re.findall(r"<([a-z_]+):(\d+)>(\S*)", t):
        assert len(rest) >= int(laenge), (name, laenge, rest)
        assert rest[:int(laenge)] == rest[:int(laenge)]
    assert "<call:10>SM6/DL1HTW" in t


def test_qth_nickname_nur_wenn_gepflegt() -> None:
    assert "qth_nickname" not in baue_adif([_qso()])
    assert "<app_eqsl_qth_nickname:4>Home" in baue_adif([_qso()], qth_nickname="Home")


def test_station_callsign_faellt_auf_operator_zurueck() -> None:
    t = baue_adif([_qso(station_callsign=None)])
    assert "<station_callsign:5>DK9XR" in t


# --------------------------------------------------------------- Antwort

def test_erfolg_wird_gelesen() -> None:
    e = lies_antwort(ANTWORT_KOPF + "<p>Result: 195 out of 200 records added</p>")
    assert (e.angenommen, e.gesamt) == (195, 200)


def test_meldungen_werden_mitgenommen() -> None:
    e = lies_antwort(
        ANTWORT_KOPF
        + "Warning: Bad record: Duplicate<br>"
        + "Result: 3 out of 4 records added"
    )
    assert e.angenommen == 3
    assert any("Duplicate" in m for m in e.meldungen)


def test_falsches_passwort_ist_ein_harter_fehler() -> None:
    """Wiederholen bringt nichts — das muss der Betreiber richten."""
    with pytest.raises(EqslError) as exc:
        lies_antwort(ANTWORT_KOPF + "Error: No match on eQSL_User/eQSL_Pswd")
    assert exc.value.hart is True


def test_wartung_ist_kein_harter_fehler() -> None:
    with pytest.raises(EqslError) as exc:
        lies_antwort(ANTWORT_KOPF + "Error: The system is down until 0200Z")
    assert exc.value.hart is False


def test_fremde_seite_wird_erkannt() -> None:
    """Ein Proxy oder eine Wartungsseite darf nicht als Erfolg durchgehen."""
    with pytest.raises(EqslError, match="keine eQSL-Importseite"):
        lies_antwort("<html><body>502 Bad Gateway</body></html>")


def test_null_angenommen_ist_kein_fehler() -> None:
    """Alles Duplikate heisst: alles ist schon dort. Kein Grund zur Panik."""
    e = lies_antwort(ANTWORT_KOPF + "Result: 0 out of 12 records added")
    assert (e.angenommen, e.gesamt) == (0, 12)


# ----------------------------------------------------------------- Upload

@pytest.mark.asyncio
async def test_leere_liste_macht_keinen_request() -> None:
    assert await upload("DK9XR", "geheim", []) == EqslErgebnis(0, 0)


@pytest.mark.asyncio
async def test_zugangsdaten_gehen_als_formularfelder(monkeypatch) -> None:
    """Nicht als ADIF-Tags im Header — so verlangt es die Spezifikation."""
    gesehen = {}

    class _Antwort:
        status_code = 200
        text = ANTWORT_KOPF + "Result: 1 out of 1 records added"

    class _Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, data=None, files=None):
            gesehen.update(url=url, data=data, files=files)
            return _Antwort()

    import ft8_appliance.integrations.eqsl as mod
    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)
    e = await upload("DK9XR", "geheim", [_qso()])
    assert e.angenommen == 1
    assert gesehen["data"] == {"EQSL_USER": "DK9XR", "EQSL_PSWD": "geheim"}
    assert "Filename" in gesehen["files"]
    assert gesehen["url"].startswith("https://"), "Passwort niemals ueber http"


@pytest.mark.asyncio
async def test_netzfehler_ist_weich(monkeypatch) -> None:
    import httpx

    import ft8_appliance.integrations.eqsl as mod

    class _Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw): raise httpx.ConnectError("weg")
    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)
    with pytest.raises(EqslError) as exc:
        await upload("DK9XR", "geheim", [_qso()])
    assert exc.value.hart is False


def test_meldungen_werden_fuer_das_log_eingedampft() -> None:
    """Beim ersten Lauf nannte eQSL 199 Duplikate einzeln beim Namen —
    28 Kilobyte in einer Logzeile, alle 15 Minuten. Das Journal liegt auf
    dem Pi auf Platte."""
    e = EqslErgebnis(1, 200, tuple(
        [f"Warning: Y=2026 M=09 D=07 CALL{i} 20M FT8 Bad record: Duplicate"
         for i in range(199)] + ["Caution: ProgramID or Logger not found"]))
    kurz = e.kurzfassung()
    assert "199 Duplikate" in kurz
    assert "ProgramID" in kurz
    assert len(kurz) < 200, f"immer noch {len(kurz)} Zeichen"


def test_kurzfassung_deckelt_auch_fremde_meldungen() -> None:
    e = EqslErgebnis(0, 9, tuple(f"Warning: Problem {i}" for i in range(9)))
    kurz = e.kurzfassung(hoechstens=3)
    assert "und 6 weitere" in kurz and kurz.count("Problem") == 3


def test_kurzfassung_ohne_meldungen_ist_leer() -> None:
    assert EqslErgebnis(5, 5).kurzfassung() == ""
