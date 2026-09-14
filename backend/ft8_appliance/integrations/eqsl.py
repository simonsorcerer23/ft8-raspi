"""eQSL.cc-Upload — Karten entstehen dort, wir liefern nur die QSOs.

Anders als bei ClubLog oder QRZ laedt man hier keine fertige Karte hoch:
Das Kartenmotiv liegt einmalig im eQSL-Profil, und eQSL druckt Rufzeichen,
Datum, Band, Betriebsart und Rapport beim Abruf selbst darauf. Wir
schicken also reine Logdaten.

Schnittstelle (dokumentiert unter eqsl.cc/qslcard/ImportADIF.txt, gelesen
2026-09-14):

* Endpunkt ``https://www.eQSL.cc/qslcard/ImportADIF.cfm``
* ``multipart/form-data``-POST, Pflichtfeld ``Filename`` mit einer ADIF-
  Datei (Header + ein oder mehrere Datensaetze).
* Zugangsdaten als Formularfelder ``EQSL_USER`` / ``EQSL_PSWD`` — ohne
  ADIF-Tag-Klammern. Das Passwort geht im Klartext mit, deshalb besteht
  eQSL ausdruecklich auf HTTPS.
* Die Antwort ist HTML. Erfolg steht als ``Result: x out of y records
  added`` darin; Fehler, Warnungen und Hinweise als eigene Zeilen.

Zwei Eigenheiten, die den Entwurf bestimmen:

Erstens meldet eQSL nur ZAHLEN, nicht welche Datensaetze abgelehnt
wurden. Ein "195 out of 200" sagt also nicht, welche fuenf fehlen. Das
ist verkraftbar, weil der haeufigste Ablehnungsgrund das Duplikat ist —
und ein Duplikat heisst, der Datensatz ist bereits dort. Wir markieren
eine angenommene Charge deshalb geschlossen als hochgeladen und
protokollieren den Rest der Antwort.

Zweitens bricht der Import laut Spezifikation kommentarlos ab, wenn ein
Datensatz kein ``<EOR>`` traegt. ``baue_adif`` setzt es deshalb selbst
und verlaesst sich nicht auf die Aufrufer.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

import httpx

from ..db.models import Qso

log = logging.getLogger(__name__)

EQSL_URL = "https://www.eQSL.cc/qslcard/ImportADIF.cfm"

# Die Antwortseite traegt diesen Kommentar; fehlt er, haben wir etwas
# anderes erwischt (Wartungsseite, Proxy, Fehlerseite).
_KENNUNG = "ADIF Real-time Interface"
_ERGEBNIS = re.compile(r"Result:\s*(\d+)\s*out of\s*(\d+)\s*records added", re.I)
_MELDUNG = re.compile(r"(Error|Warning|Caution|Information):\s*([^<\r\n]+)", re.I)

# Meldungen, bei denen ein erneuter Versuch nichts bringt. Alles andere
# (Netz, Wartung, 5xx) ist vorruebergehend und wird wiederholt.
_HART = ("no match on eqsl_user", "bad callsign", "not a valid")


class EqslError(RuntimeError):
    """eQSL hat den Upload abgelehnt."""

    def __init__(self, text: str, *, hart: bool = False) -> None:
        super().__init__(text)
        self.hart = hart


@dataclass(frozen=True, slots=True)
class EqslErgebnis:
    angenommen: int
    gesamt: int
    meldungen: tuple[str, ...] = ()


def _feld(name: str, wert: object) -> str:
    """Ein ADIF-Feld. Leere Werte fallen weg, sonst zaehlt die Laenge falsch."""
    if wert is None:
        return ""
    s = str(wert).strip()
    return f"<{name}:{len(s)}>{s}" if s else ""


def _band_ohne_einheit(band: str | None) -> str:
    return (band or "").strip().lower()


def baue_adif(qsos: list[Qso], *, qth_nickname: str | None = None) -> str:
    """Die QSOs als ADIF, reduziert auf das, was eQSL auswertet.

    Bewusst sparsam: Unbekannte Felder erzeugen im Import-Bericht nur
    Rauschen. ``APP_EQSL_QTH_NICKNAME`` trennt mehrere Konten desselben
    Rufzeichens (Heim-QTH, portabel) und wird nur gesetzt, wenn der
    Betreiber eines gepflegt hat.
    """
    zeilen = [
        "ADIF-Export der FT8-Appliance fuer eQSL.cc",
        "<ADIF_VER:5>3.1.1",
        "<PROGRAMID:13>ft8-appliance",
        "<eoh>",
        "",
    ]
    for q in qsos:
        ts: datetime = q.qso_start
        teile = [
            _feld("station_callsign", q.station_callsign or q.user_callsign),
            _feld("call", q.call),
            _feld("qso_date", f"{ts:%Y%m%d}"),
            _feld("time_on", f"{ts:%H%M}"),
            _feld("band", _band_ohne_einheit(q.band)),
            _feld("mode", q.mode),
            _feld("rst_sent", q.rst_sent),
            _feld("rst_rcvd", q.rst_rcvd),
        ]
        if qth_nickname:
            teile.append(_feld("app_eqsl_qth_nickname", qth_nickname))
        # <EOR> ohne Laengenangabe — ein fehlendes beendet den Import
        # der ganzen Datei ohne Meldung.
        zeilen.append(" ".join(t for t in teile if t) + " <eor>")
    return "\n".join(zeilen) + "\n"


def lies_antwort(text: str) -> EqslErgebnis:
    """Die HTML-Antwort auswerten. Wirft EqslError, wenn nichts ankam."""
    meldungen = tuple(f"{art}: {rest.strip()}" for art, rest in _MELDUNG.findall(text))
    treffer = _ERGEBNIS.search(text)
    if treffer is None:
        if _KENNUNG not in text:
            raise EqslError(
                "unerwartete Antwort (keine eQSL-Importseite) — "
                + (meldungen[0] if meldungen else text.strip()[:200])
            )
        grund = meldungen[0] if meldungen else text.strip()[:200]
        raise EqslError(grund, hart=any(h in grund.lower() for h in _HART))
    return EqslErgebnis(int(treffer.group(1)), int(treffer.group(2)), meldungen)


async def upload(
    user: str, password: str, qsos: list[Qso], *,
    qth_nickname: str | None = None, timeout: float = 60.0,
) -> EqslErgebnis:
    """Eine Charge QSOs hochladen. Leere Liste = nichts tun."""
    if not qsos:
        return EqslErgebnis(0, 0)
    adif = baue_adif(qsos, qth_nickname=qth_nickname)
    daten = {"EQSL_USER": user, "EQSL_PSWD": password}
    dateien = {"Filename": ("ft8-appliance.adi", adif.encode("utf-8"), "text/plain")}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            antwort = await client.post(EQSL_URL, data=daten, files=dateien)
    except httpx.HTTPError as exc:
        raise EqslError(f"Netzfehler: {exc}") from exc
    if antwort.status_code >= 400:
        raise EqslError(f"HTTP {antwort.status_code}")
    return lies_antwort(antwort.text)
