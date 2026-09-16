"""eQSL-Posteingang: Was andere uns bestaetigt haben, samt Kartenmotiv.

Das Gegenstueck zu :mod:`eqsl`, das nur hochlaedt. Hier holen wir, was
zurueckkommt — erst die Liste, dann auf Wunsch die Bilder.

Zwei Schnittstellen, beide 2026-09-16 aus der eQSL-Dokumentation gelesen:

``DownloadInBox.cfm`` (Spezifikation ``DownloadInBox.txt``)
    Liefert den Posteingang als ADIF. Die Antwort ist allerdings **nicht**
    die Datei, sondern eine HTML-Seite mit zwei Links darauf; Erfolg
    erkennt man am Satz "Your ADIF log file has been built". Die erzeugte
    Datei verschwindet nach wenigen Stunden wieder, und die Ordnerstruktur
    darf laut Doku nicht fest verdrahtet werden — deshalb folgen wir dem
    Link aus der Antwort, statt ihn zu bauen.

``GeteQSL.cfm`` (Spezifikation ``GeteQSL.txt``)
    Liefert das Kartenbild zu **einer** Verbindung. Zeitstempel muessen
    auf fuenf Minuten genau passen, der Modus exakt. Auch hier ist die
    Antwort HTML: erst auf "Error:" pruefen, sonst die Adresse aus dem
    ``<img src=…>`` nehmen.

**Die Bildadresse ist fluechtig.** eQSL erzeugt die Grafik im Moment des
Abrufs in einem temporaeren Ordner und raeumt ihn periodisch ab. Wer die
Adresse von aussen verlinkt, zeigt nach ein paar Stunden ins Leere — die
Bilder muessen also lokal liegen. Das ist kein Umweg, sondern die einzige
Betriebsart, die funktioniert.

**Tempolimit, woertlich aus der Spezifikation:** hoechstens sechs Karten
je Minute ("SLOWER THAN 6 PER MINUTE"), keine parallelen Abrufe, und
ausdruecklich keine Massendownloads ganzer Posteingaenge. Die Erzeugung
der Grafiken kostet eQSL Rechenzeit; bei Ueberlast drosseln sie selbst.
:data:`MINDESTABSTAND_S` haelt das ein, und der Aufrufer holt nur, was
noch fehlt — schon geholte Karten liegen auf der Platte.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

log = logging.getLogger(__name__)

INBOX_URL = "https://www.eQSL.cc/qslcard/DownloadInBox.cfm"
KARTE_URL = "https://www.eQSL.cc/qslcard/GeteQSL.cfm"
BASIS = "https://www.eQSL.cc/qslcard/"

# Die Spezifikation verlangt LANGSAMER als sechs je Minute. Zehn Sekunden
# sind genau an der Grenze, zwoelf halten sie ein, ohne zu bummeln.
MINDESTABSTAND_S = 12.0

_ERFOLG = "your adif log file has been built"


class EqslInboxError(RuntimeError):
    """Abruf gescheitert. ``hart`` = erneuter Versuch ist sinnlos."""

    def __init__(self, text: str, *, hart: bool = False) -> None:
        super().__init__(text)
        self.hart = hart


@dataclass(frozen=True, slots=True)
class Karteneintrag:
    """Eine eingegangene Bestaetigung — die Metadaten, noch ohne Bild."""

    call: str
    qso_date: str          # YYYYMMDD
    time_on: str           # HHMM
    band: str
    mode: str
    empfangen_am: str | None   # EQSL_QSLRDATE, YYYYMMDD
    gridsquare: str | None
    nachricht: str | None      # QSLMSG des Absenders

    @property
    def schluessel(self) -> str:
        """Eindeutig je Verbindung — dient als Dateiname und DB-Schluessel."""
        return f"{self.call}_{self.qso_date}_{self.time_on}_{self.band}_{self.mode}".upper()


def _feld(satz: str, name: str) -> str | None:
    """Ein ADIF-Feld lesen — laengenbasiert, wie das Format es vorsieht.

    Stimmt die Laengenangabe nicht, laeuft der Wert in das naechste Tag
    hinein und enthaelt ein ``<``. Solche Werte werden verworfen statt
    weitergereicht: Sie landen sonst in Rufzeichen und von dort in
    Dateinamen.
    """
    m = re.search(rf"<{name}:(\d+)(?::[^>]*)?>", satz, re.I)
    if not m:
        return None
    wert = satz[m.end():m.end() + int(m.group(1))].strip()
    if not wert or "<" in wert:
        return None
    return wert


def lies_adif(adif: str) -> list[Karteneintrag]:
    """ADIF des Posteingangs in Eintraege zerlegen.

    Saetze ohne Rufzeichen, Datum oder Zeit werden uebersprungen: ohne
    diese drei laesst sich das Bild spaeter nicht anfordern.
    """
    koerper = adif.split("<eoh>", 1)[-1] if "<eoh>" in adif.lower() else adif
    aus: list[Karteneintrag] = []
    for satz in re.split(r"<eor>", koerper, flags=re.I):
        if not satz.strip():
            continue
        call = _feld(satz, "call")
        datum = _feld(satz, "qso_date")
        zeit = _feld(satz, "time_on")
        if not (call and datum and zeit):
            continue
        aus.append(Karteneintrag(
            call=call.upper(),
            qso_date=datum,
            time_on=zeit[:4].zfill(4),
            band=(_feld(satz, "band") or "").upper(),
            mode=(_feld(satz, "mode") or "").upper(),
            empfangen_am=_feld(satz, "eqsl_qslrdate"),
            gridsquare=_feld(satz, "gridsquare"),
            nachricht=_feld(satz, "qslmsg"),
        ))
    return aus


async def hole_inbox(
    user: str, password: str, *, seit: datetime | None = None,
    qth_nickname: str | None = None, timeout: float = 180.0,
) -> list[Karteneintrag]:
    """Posteingang als Liste holen — ein Abruf, auch fuer tausende Karten.

    ``seit`` filtert ueber ``RcvdSince`` auf den Eingangszeitpunkt bei
    eQSL, nicht auf das QSO-Datum. Genau das braucht der laufende
    Betrieb: "was ist seit gestern dazugekommen".
    """
    p: dict[str, str] = {"UserName": user, "Password": password, "HamOnly": "1"}
    if qth_nickname:
        p["QTHNickname"] = qth_nickname
    if seit is not None:
        p["RcvdSince"] = seit.astimezone(UTC).strftime("%Y%m%d%H%M")
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            antwort = await client.get(INBOX_URL, params=p)
            antwort.raise_for_status()
            seite = antwort.text
            if _ERFOLG not in seite.lower():
                klartext = re.sub(r"<[^>]+>", " ", seite)
                klartext = re.sub(r"\s+", " ", klartext).strip()[:200]
                raise EqslInboxError(f"eQSL meldet: {klartext}", hart=True)
            m = re.search(r'href="([^"]+\.adi)"', seite, re.I)
            if not m:
                raise EqslInboxError("kein ADIF-Link in der Antwort", hart=True)
            ziel = httpx.URL(BASIS).join(m.group(1))
            datei = await client.get(ziel)
            datei.raise_for_status()
            return lies_adif(datei.text)
    except httpx.HTTPError as exc:
        raise EqslInboxError(f"Netzfehler: {exc}") from exc


# Nicht jeder Fehler lohnt einen zweiten Versuch. Diese hier sind
# endgueltig: die Karte gibt es so nicht, oder sie wurde zurueckgewiesen.
_HARTE_FEHLER = (
    "cannot find that log entry",
    "has been rejected",
    "no match on username",
    "not authorized",
    "missing ",
)


async def hole_karte(
    user: str, password: str, eintrag: Karteneintrag, *,
    qth_nickname: str | None = None, timeout: float = 90.0,
) -> tuple[bytes, str]:
    """Das Kartenbild zu einer Verbindung holen.

    Gibt die Bilddaten und den Medientyp zurueck. Das Format ist in der
    Spezifikation nicht festgelegt — in der Praxis JPEG, aber wir lesen
    den Typ aus der Antwort statt ihn anzunehmen.
    """
    p = {
        "Username": user, "Password": password, "CallsignFrom": eintrag.call,
        "QSOYear": eintrag.qso_date[:4], "QSOMonth": eintrag.qso_date[4:6],
        "QSODay": eintrag.qso_date[6:8], "QSOHour": eintrag.time_on[:2],
        "QSOMinute": eintrag.time_on[2:4], "QSOBand": eintrag.band,
        "QSOMode": eintrag.mode,
    }
    if qth_nickname:
        p["QTHNickname"] = qth_nickname
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            antwort = await client.get(KARTE_URL, params=p)
            antwort.raise_for_status()
            seite = antwort.text
            fehler = re.search(r"Error:\s*([^<\n]+)", seite, re.I)
            if fehler:
                text = fehler.group(1).strip()
                hart = any(h in text.lower() for h in _HARTE_FEHLER)
                raise EqslInboxError(text, hart=hart)
            if "throttling invoked" in seite.lower():
                # eQSL drosselt gerade selbst — kein harter Fehler, aber
                # ein deutlicher Wink, langsamer zu machen.
                raise EqslInboxError("eQSL drosselt (Processor Overload)")
            m = re.search(r'<img\s+src="?([^">\s]+)', seite, re.I)
            if not m:
                raise EqslInboxError("kein Bild in der Antwort", hart=True)
            bild = await client.get(httpx.URL(BASIS).join(m.group(1)))
            bild.raise_for_status()
            typ = (bild.headers.get("content-type") or "image/jpeg").split(";")[0]
            if not typ.startswith("image/"):
                raise EqslInboxError(f"unerwarteter Typ {typ}", hart=True)
            return bild.content, typ
    except httpx.HTTPError as exc:
        raise EqslInboxError(f"Netzfehler: {exc}") from exc
