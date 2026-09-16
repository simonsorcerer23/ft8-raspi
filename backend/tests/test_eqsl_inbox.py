"""eQSL-Posteingang: ADIF lesen, Bilder holen, Tempolimit einhalten.

Der Posteingang ist die Gegenrichtung zum Upload. Zwei Eigenheiten der
Schnittstelle bestimmen den Entwurf und werden hier festgehalten:

* Beide Aufrufe antworten mit **HTML**, nicht mit den Nutzdaten. Erfolg
  erkennt man an einem Satz bzw. am ``<img src=…>``; wer auf HTTP 200
  prueft, haelt eine Fehlerseite fuer eine Karte.
* Die Bildadresse ist **fluechtig** — sie wird nach Stunden abgeraeumt.
  Deshalb muessen die Bytes lokal landen, und deshalb darf kein Aufrufer
  die eQSL-Adresse weiterreichen.
"""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from ft8_appliance.integrations.eqsl_inbox import (
    MINDESTABSTAND_S,
    EqslInboxError,
    Karteneintrag,
    hole_inbox,
    hole_karte,
    lies_adif,
)

ADIF = (
    "eQSL.cc DownloadInBox\n<ADIF_Ver:5>3.1.6\n<eoh>\n"
    "<CALL:5>TA1CQ<QSO_DATE:8>20260916<TIME_ON:6>062000<BAND:3>20M"
    "<MODE:3>FT8<EQSL_QSL_RCVD:1>Y<EQSL_QSLRDATE:8>20260916"
    "<GRIDSQUARE:4>KN41<QSLMSG:13>73 und danke!<eor>\n"
    "<CALL:5>W3FOX<QSO_DATE:8>20260915<TIME_ON:4>1830<BAND:3>17M"
    "<MODE:3>FT8<EQSL_QSLRDATE:8>20260916<eor>\n"
)


# --------------------------------------------------------------- ADIF

def test_liest_die_eintraege() -> None:
    e = lies_adif(ADIF)
    assert [k.call for k in e] == ["TA1CQ", "W3FOX"]
    assert e[0].band == "20M" and e[0].mode == "FT8"
    assert e[0].gridsquare == "KN41"
    assert e[0].nachricht == "73 und danke!"


def test_zeit_wird_auf_vier_stellen_gekuerzt() -> None:
    """eQSL liefert TIME_ON mal mit, mal ohne Sekunden. Der Abruf des
    Bildes will HH und MM getrennt — eine sechsstellige Zeit haette
    dort die Sekunden als Minuten eingesetzt."""
    e = lies_adif(ADIF)
    assert e[0].time_on == "0620"
    assert e[1].time_on == "1830"


def test_satz_ohne_pflichtfelder_faellt_weg() -> None:
    """Ohne Rufzeichen, Datum und Zeit laesst sich das Bild spaeter gar
    nicht anfordern — so ein Satz darf gar nicht erst in die Liste."""
    kaputt = "<eoh>\n<BAND:3>20M<MODE:3>FT8<eor>\n" + ADIF.split("<eoh>", 1)[1]
    assert len(lies_adif(kaputt)) == 2


def test_falsche_laengenangabe_verwirft_das_feld() -> None:
    """Eine zu grosse Laenge zieht das naechste Tag in den Wert. Ohne
    Schutz stuende ein "<" im Rufzeichen — und damit im Dateinamen des
    gespeicherten Bildes. Aufgefallen an eigenen Testdaten, in denen
    CALL:6 fuer ein fuenfstelliges Rufzeichen stand."""
    kaputt = ("<eoh>\n<CALL:6>TA1CQ<QSO_DATE:8>20260916<TIME_ON:4>0620"
              "<BAND:3>20M<MODE:3>FT8<eor>\n")
    assert lies_adif(kaputt) == []


def test_schluessel_ist_eindeutig_je_verbindung() -> None:
    a, b = lies_adif(ADIF)
    assert a.schluessel != b.schluessel
    assert a.schluessel == "TA1CQ_20260916_0620_20M_FT8"


def test_adif_ohne_kopf_wird_auch_gelesen() -> None:
    ohne = ADIF.split("<eoh>", 1)[1]
    assert len(lies_adif(ohne)) == 2


# ------------------------------------------------------------ Inbox

def _client(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    echt = httpx.AsyncClient

    class Gemockt(echt):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", Gemockt)


@pytest.mark.asyncio
async def test_inbox_folgt_dem_link_aus_der_antwort(monkeypatch) -> None:
    """Die Ordnerstruktur darf laut Spezifikation nicht gebaut werden —
    eQSL hat sie 2019 verschoben. Also dem Link folgen."""
    gesehen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        gesehen.append(str(req.url))
        if "DownloadInBox" in str(req.url):
            return httpx.Response(200, text=(
                "<html>Your ADIF log file has been built<br>"
                '<a href="../DownloadedFiles/abc123.adi">ADI</a></html>'))
        return httpx.Response(200, text=ADIF)

    _client(monkeypatch, handler)
    e = await hole_inbox("DK9XR", "geheim")
    assert len(e) == 2
    assert "DownloadedFiles/abc123.adi" in gesehen[1]


@pytest.mark.asyncio
async def test_inbox_ohne_erfolgsmeldung_ist_hart(monkeypatch) -> None:
    """HTTP 200 mit Fehlertext ist der Normalfall bei eQSL. Wer nur den
    Statuscode prueft, haelt eine Fehlerseite fuer einen Posteingang."""
    _client(monkeypatch, lambda r: httpx.Response(
        200, text="<html><body>Bad username or password</body></html>"))
    with pytest.raises(EqslInboxError) as exc:
        await hole_inbox("DK9XR", "falsch")
    assert exc.value.hart is True
    assert "Bad username" in str(exc.value)


@pytest.mark.asyncio
async def test_rcvdsince_filtert_auf_den_eingang(monkeypatch) -> None:
    """Nicht auf das QSO-Datum: eine Bestaetigung von heute kann eine
    Verbindung von 1976 betreffen — und genau solche stehen im Log."""
    gesehen: list[httpx.URL] = []

    def handler(req: httpx.Request) -> httpx.Response:
        gesehen.append(req.url)
        if "DownloadInBox" in str(req.url):
            return httpx.Response(200, text=(
                'Your ADIF log file has been built <a href="x.adi">a</a>'))
        return httpx.Response(200, text=ADIF)

    _client(monkeypatch, handler)
    await hole_inbox("DK9XR", "geheim",
                     seit=datetime(2026, 9, 15, 6, 30, tzinfo=UTC))
    assert gesehen[0].params["RcvdSince"] == "202609150630"


# ------------------------------------------------------------ Karte

EINTRAG = Karteneintrag("TA1CQ", "20260916", "0620", "20M", "FT8",
                        "20260916", "KN41", None)


@pytest.mark.asyncio
async def test_karte_laedt_die_bytes_statt_der_adresse(monkeypatch) -> None:
    """Die Adresse wird nach Stunden abgeraeumt — wer sie weiterreicht,
    zeigt bald ins Leere. Also die Bytes holen."""
    def handler(req: httpx.Request) -> httpx.Response:
        if "GeteQSL" in str(req.url):
            return httpx.Response(200, text='<html><IMG SRC="/tmp/x9.JPG"></html>')
        return httpx.Response(200, content=b"\xff\xd8JPEGDATA",
                              headers={"content-type": "image/jpeg"})

    _client(monkeypatch, handler)
    daten, typ = await hole_karte("DK9XR", "geheim", EINTRAG)
    assert daten == b"\xff\xd8JPEGDATA"
    assert typ == "image/jpeg"


@pytest.mark.asyncio
async def test_zurueckgewiesene_karte_ist_hart(monkeypatch) -> None:
    """Die kommt nie — ein zweiter Versuch waere verschwendetes Tempo
    aus einem Kontingent von sechs Karten je Minute."""
    _client(monkeypatch, lambda r: httpx.Response(
        200, text="<html>Error: That QSO has been Rejected by DK9XR</html>"))
    with pytest.raises(EqslInboxError) as exc:
        await hole_karte("DK9XR", "geheim", EINTRAG)
    assert exc.value.hart is True


@pytest.mark.asyncio
async def test_kein_eintrag_gefunden_ist_hart(monkeypatch) -> None:
    _client(monkeypatch, lambda r: httpx.Response(
        200, text="<html>Error: I cannot find that log entry</html>"))
    with pytest.raises(EqslInboxError) as exc:
        await hole_karte("DK9XR", "geheim", EINTRAG)
    assert exc.value.hart is True


@pytest.mark.asyncio
async def test_drosselung_ist_weich(monkeypatch) -> None:
    """eQSL drosselt bei Ueberlast selbst und sagt es im Kommentar. Das
    ist kein Fehler der Karte, sondern die Bitte, langsamer zu machen."""
    _client(monkeypatch, lambda r: httpx.Response(
        200, text="<!-- Warning: Processor Overload - Throttling invoked -->"))
    with pytest.raises(EqslInboxError) as exc:
        await hole_karte("DK9XR", "geheim", EINTRAG)
    assert exc.value.hart is False


@pytest.mark.asyncio
async def test_nichtbild_wird_abgelehnt(monkeypatch) -> None:
    """Wenn hinter der Adresse HTML steckt, waere es als Karte gespeichert
    worden und die Galerie zeigte ein kaputtes Bild."""
    def handler(req: httpx.Request) -> httpx.Response:
        if "GeteQSL" in str(req.url):
            return httpx.Response(200, text='<IMG SRC="/tmp/x.JPG">')
        return httpx.Response(200, text="<html>weg</html>",
                              headers={"content-type": "text/html"})

    _client(monkeypatch, handler)
    with pytest.raises(EqslInboxError) as exc:
        await hole_karte("DK9XR", "geheim", EINTRAG)
    assert exc.value.hart is True


@pytest.mark.asyncio
async def test_alle_pflichtparameter_gehen_mit(monkeypatch) -> None:
    """Fehlt einer, antwortet eQSL mit "Missing …" — und zwar erst nach
    dem Aufruf, also auf Kosten des Tempolimits."""
    gesehen: list[httpx.URL] = []

    def handler(req: httpx.Request) -> httpx.Response:
        gesehen.append(req.url)
        if "GeteQSL" in str(req.url):
            return httpx.Response(200, text='<IMG SRC="/tmp/x.JPG">')
        return httpx.Response(200, content=b"x", headers={"content-type": "image/jpeg"})

    _client(monkeypatch, handler)
    await hole_karte("DK9XR", "geheim", EINTRAG)
    p = gesehen[0].params
    for feld in ("Username", "Password", "CallsignFrom", "QSOYear", "QSOMonth",
                 "QSODay", "QSOHour", "QSOMinute", "QSOBand", "QSOMode"):
        assert p.get(feld), f"{feld} fehlt im Aufruf"
    assert p["QSOYear"] == "2026" and p["QSOHour"] == "06" and p["QSOMinute"] == "20"


def test_tempolimit_haelt_die_vorgabe_ein() -> None:
    """eQSL verlangt LANGSAMER als sechs je Minute — also mehr als zehn
    Sekunden Abstand, nicht genau zehn."""
    assert MINDESTABSTAND_S > 10.0
