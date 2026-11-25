"""Die Station meldet selbst an die Zentrale (Schnittstelle v1, seit 2026-11-25).

Bis dahin fragte ein Spiegel auf dem Webserver die Station jede Minute ab.
War der Pi weg, wurde jede Abfrage zum Fehler, und Logbuch und Karten lagen
nur auf der Station. Jetzt schickt sie, solange sie online ist, und die
Zentrale haelt den Hauptbestand.

Es gibt keine eigene Warteschlange. Die SQLite-Datenbank bleibt die Quelle,
die Zentrale fuehrt den Stand: Die Station fragt, was dort schon liegt, und
schickt den Rest. Faellt das Netz aus, geht nichts verloren; danach holt sie
von selbst nach. Alle Schreibaufrufe der Zentrale sind Upserts, eine
Wiederholung schadet nicht.

Endpunkte (JSON mit ``"v": 1``, ``Authorization: Bearer <Stationstoken>``):

* ``POST /v1/meldung`` alle 60 s — Livedaten (gefiltert wie ``live.json``)
  und der interne Betriebszustand; zugleich das Lebenszeichen.
* ``GET /v1/qso/stand?db_kennung=`` und ``POST /v1/qso`` — QSOs in Stapeln.
* ``POST /v1/qso/abgleich`` einmal am Tag — (id, hash) aller QSOs; die
  Zentrale nennt, was fehlt oder abweicht.
* ``GET /v1/karten/stand``, ``POST /v1/karten``, ``PUT /v1/bild/<sha256>`` —
  eQSL-Karten mit Operator, Bilder nach Inhalt adressiert.
* ``POST /v1/profil`` stuendlich — das Tagesprofil wie ``profil.json``.

Der Sender laeuft als eigener Hintergrundtask. Er wartet nie auf den
Slot-Takt und der Slot-Takt nie auf ihn; jeder Fehler endet hier im Log.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import httpx

from .._version import __version__
from . import aufbereitung

log = logging.getLogger(__name__)

VERSION = 1
MELDUNG_S = 60.0
QSO_S = 600.0
KARTEN_S = 600.0
PROFIL_S = 3600.0
ABGLEICH_S = 86400.0
STAPEL_QSO = 500
STAPEL_KARTEN = 200
BILDER_JE_RUNDE = 20
BILD_MAX = 2 * 1024 * 1024
BILD_TYPEN = {"image/jpeg", "image/png"}
PAUSE_MAX_S = 900.0
QSO_FELDER = ("call", "band", "freq_hz", "mode", "rst_sent", "rst_rcvd", "grid_rcvd",
              "qso_start", "qso_end", "my_grid", "my_power_w", "user_callsign", "notes")


class ZentraleFehler(RuntimeError):
    def __init__(self, text: str, *, warte_s: float | None = None, dauerhaft: bool = False,
                 status: int | None = None):
        super().__init__(text)
        self.warte_s = warte_s
        self.dauerhaft = dauerhaft
        self.status = status


def zeit_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def qso_zeile(q: Any) -> dict:
    """Eine QSO-Zeile fuer die Zentrale, mit Hash ueber genau diese Felder.

    Nicht dabei: die Upload-Zustaende (gehoeren der Station) und my_lat/my_lon
    (fuer den Bestand reicht my_grid).
    """
    z: dict[str, Any] = {}
    for f in QSO_FELDER:
        w = getattr(q, f, None)
        z[f] = zeit_utc(w) if isinstance(w, datetime) else w
    z["hash"] = hashlib.sha256(
        json.dumps(z, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    z["qso_id"] = q.id
    return z


def _qso_spalten() -> tuple:
    from ..db.models import Qso
    return (Qso.id,) + tuple(getattr(Qso, f) for f in QSO_FELDER)


class Zentrale:
    def __init__(self, url: str, token: str, *, grid: str, mit_rufzeichen: bool = True,
                 qsl_verzeichnis: Callable[[], Path],
                 lokal: str = "http://127.0.0.1:8000",
                 zustand_pfad: Path = Path("/var/lib/ft8-appliance/zentrale.json"),
                 transport: httpx.AsyncBaseTransport | None = None,
                 lokal_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url = url.rstrip("/")
        self._token = token
        self.grid = grid
        self.mit_rufzeichen = mit_rufzeichen
        self._qsl_verzeichnis = qsl_verzeichnis
        self._lokal = lokal.rstrip("/")
        self._zustand_pfad = zustand_pfad
        self._transport = transport
        self._lokal_transport = lokal_transport
        self._zustand = self._lade_zustand()
        self._faellig: dict[str, float] = {}
        self._sha_cache: dict[tuple[str, int], str] = {}
        self._ungeeignet: set[str] = set()
        self._fehler_in_folge = 0
        self.letzte_ok: str | None = None
        self.letzter_fehler: str | None = None
        self.rueckstand_qso: int | None = None
        self.karten_offen: int | None = None

    # ------------------------------------------------------------------ Zustand
    def _lade_zustand(self) -> dict:
        try:
            d = json.loads(self._zustand_pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        if not d.get("db_kennung"):
            d["db_kennung"] = str(uuid.uuid4())
            # Der erste Abgleich erst nach einem Tag: Die erste Runde schickt
            # ohnehin alles.
            d["abgleich_at"] = time.time()
            self._speichere(d)
        return d

    def _speichere(self, d: dict | None = None) -> None:
        d = self._zustand if d is None else d
        try:
            self._zustand_pfad.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._zustand_pfad.with_suffix(".teil")
            tmp.write_text(json.dumps(d), encoding="utf-8")
            tmp.replace(self._zustand_pfad)
        except OSError as exc:
            log.warning("Zentrale: Zustand nicht gespeichert: %s", exc)

    @property
    def db_kennung(self) -> str:
        return self._zustand["db_kennung"]

    def status(self) -> dict:
        return {"letzte_ok": self.letzte_ok, "letzter_fehler": self.letzter_fehler,
                "rueckstand_qso": self.rueckstand_qso, "karten_offen": self.karten_offen}

    # --------------------------------------------------------------------- HTTP
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.url, timeout=30.0, transport=self._transport,
            headers={"Authorization": f"Bearer {self._token}"})

    async def _rufe(self, c: httpx.AsyncClient, methode: str, pfad: str, **kw) -> Any:
        r = await c.request(methode, pfad, **kw)
        if r.status_code == 401:
            raise ZentraleFehler("Token abgelehnt (401)", warte_s=PAUSE_MAX_S, dauerhaft=True)
        if r.status_code == 409:
            raise ZentraleFehler(f"Schnittstelle v{VERSION} unbekannt (409)",
                                 warte_s=PAUSE_MAX_S, dauerhaft=True)
        if r.status_code == 429:
            try:
                warte = float(r.headers.get("Retry-After") or 60)
            except ValueError:
                warte = 60.0
            raise ZentraleFehler("Zentrale ausgelastet (429)", warte_s=warte)
        if r.status_code >= 400:
            raise ZentraleFehler(f"{methode} {pfad}: HTTP {r.status_code}", status=r.status_code)
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    async def _lokal_holen(self, lc: httpx.AsyncClient, pfad: str, timeout: float = 20.0) -> dict:
        r = await lc.get(self._lokal + pfad, timeout=timeout)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------- Teile
    async def meldung(self, c: httpx.AsyncClient, lc: httpx.AsyncClient) -> None:
        status = await self._lokal_holen(lc, "/api/status")
        stats = await self._lokal_holen(lc, "/api/stats")
        karte = await self._lokal_holen(lc, "/api/map")
        psk = await self._lokal_holen(lc, "/api/psk/who-heard-me")
        rumpf = {
            "v": VERSION, "gesendet": aufbereitung.stand_jetzt(),
            "version": __version__,
            "betrieb": aufbereitung.baue_betrieb(status),
            "live": aufbereitung.baue_live(status, stats, karte, psk, grid=self.grid,
                                           mit_rufzeichen=self.mit_rufzeichen),
        }
        await self._rufe(c, "POST", "/v1/meldung", json=rumpf)

    async def profil(self, c: httpx.AsyncClient, lc: httpx.AsyncClient) -> None:
        roh = await self._lokal_holen(lc, "/api/psk/tagesprofil?tage=7", timeout=60.0)
        await self._rufe(c, "POST", "/v1/profil",
                         json={"v": VERSION, "profil": aufbereitung.baue_profil(roh)})

    async def _qsos(self, *, ab_id: int | None = None, ids: list[int] | None = None,
                    grenze: int = STAPEL_QSO) -> list[dict]:
        from sqlalchemy import select

        from ..db import session_scope

        q = select(*_qso_spalten()).order_by(_qso_spalten()[0])
        if ids is not None:
            q = q.where(_qso_spalten()[0].in_(ids))
        else:
            q = q.where(_qso_spalten()[0] > (ab_id or 0)).limit(grenze)
        async with session_scope() as s:
            reihen = (await s.execute(q)).all()
        await asyncio.sleep(0)
        return [qso_zeile(r) for r in reihen]

    async def _qso_rueckstand(self, ab_id: int) -> int:
        from sqlalchemy import func, select

        from ..db import session_scope
        from ..db.models import Qso

        async with session_scope() as s:
            return (await s.execute(
                select(func.count()).select_from(Qso).where(Qso.id > ab_id))).scalar() or 0

    async def _qso_schicken(self, c: httpx.AsyncClient, zeilen: list[dict]) -> int:
        antwort = await self._rufe(c, "POST", "/v1/qso", json={
            "v": VERSION, "db_kennung": self.db_kennung, "zeilen": zeilen}) or {}
        return int(antwort.get("letzte_id") or max(z["qso_id"] for z in zeilen))

    async def qsos(self, c: httpx.AsyncClient, *, stapel_max: int = 20) -> None:
        stand = await self._rufe(c, "GET", "/v1/qso/stand",
                                 params={"db_kennung": self.db_kennung}) or {}
        letzte = int(stand.get("letzte_id") or 0)
        for _ in range(stapel_max):
            zeilen = await self._qsos(ab_id=letzte)
            if not zeilen:
                break
            letzte = max(letzte, await self._qso_schicken(c, zeilen),
                         zeilen[-1]["qso_id"])
        self.rueckstand_qso = await self._qso_rueckstand(letzte)

    async def abgleich(self, c: httpx.AsyncClient) -> None:
        # In Stuecken zu 1000 mit Pause dazwischen: Der Sender teilt sich die
        # Ereignisschleife mit dem Slot-Takt, und 20 000 Zeilen am Stueck
        # wuerden ihn um eine Sekunde und mehr aufhalten.
        eintraege: list[list] = []
        letzte = 0
        while True:
            zeilen = await self._qsos(ab_id=letzte, grenze=1000)
            if not zeilen:
                break
            eintraege.extend([z["qso_id"], z["hash"]] for z in zeilen)
            letzte = zeilen[-1]["qso_id"]
            await asyncio.sleep(0.05)
        antwort = await self._rufe(c, "POST", "/v1/qso/abgleich", json={
            "v": VERSION, "db_kennung": self.db_kennung, "eintraege": eintraege}) or {}
        nachsenden = sorted(set(antwort.get("fehlt") or []) | set(antwort.get("anders") or []))
        for i in range(0, len(nachsenden), STAPEL_QSO):
            zeilen = await self._qsos(ids=nachsenden[i:i + STAPEL_QSO])
            if zeilen:
                await self._qso_schicken(c, zeilen)
        if nachsenden or antwort.get("nur_zentral"):
            log.info("Zentrale: Abgleich %d nachgesandt, %d nur zentral",
                     len(nachsenden), len(antwort.get("nur_zentral") or []))
        self._zustand["abgleich_at"] = time.time()
        self._speichere()

    def _bild(self, datei: str | None) -> Path | None:
        if not datei:
            return None
        wurzel = self._qsl_verzeichnis().resolve()
        pfad = (wurzel / datei).resolve()
        if not pfad.is_relative_to(wurzel) or not pfad.is_file():
            return None
        return pfad

    def _sha(self, pfad: Path) -> str:
        """Hash eines Kartenbildes, je Datei nur einmal (die Bilder aendern sich nie)."""
        schluessel = (str(pfad), pfad.stat().st_mtime_ns)
        if schluessel not in self._sha_cache:
            self._sha_cache[schluessel] = hashlib.sha256(pfad.read_bytes()).hexdigest()
        return self._sha_cache[schluessel]

    async def karten(self, c: httpx.AsyncClient) -> bool:
        """Je Runde hoechstens STAPEL_KARTEN offene Karten und BILDER_JE_RUNDE Bilder.

        Am MK2 liegen ueber 8000 Karten. Beim ersten Mal geht der Bestand
        so in Stuecken hinaus statt in einem Rutsch; True heisst, es ist noch
        etwas offen, und die naechste Runde macht weiter.
        """
        from sqlalchemy import select

        from ..db import session_scope
        from ..db.models import QslKarte as K

        stand = await self._rufe(c, "GET", "/v1/karten/stand") or {}
        bekannt = set(stand.get("schluessel") or [])
        mit_bild = set(stand.get("mit_bild") or [])
        spalten = (K.schluessel, K.call, K.qso_date, K.time_on, K.band, K.mode,
                   K.user_callsign, K.empfangen_am, K.gridsquare, K.nachricht,
                   K.medientyp, K.bytes, K.datei)
        async with session_scope() as s:
            reihen = (await s.execute(select(*spalten).order_by(K.id))).all()
        await asyncio.sleep(0)
        offen = [dict(r._mapping) for r in reihen
                 if r.schluessel not in bekannt
                 or (r.datei and r.schluessel not in mit_bild
                     and r.schluessel not in self._ungeeignet)]
        self.karten_offen = len(offen)
        teil = offen[:STAPEL_KARTEN]
        if not teil:
            return False
        pfade: dict[str, Path] = {}
        zeilen = []
        for k in teil:
            pfad = self._bild(k.pop("datei"))
            sha = None
            if pfad is not None and (k["medientyp"] or "image/jpeg") in BILD_TYPEN \
                    and 0 < pfad.stat().st_size <= BILD_MAX:
                sha = await asyncio.to_thread(self._sha, pfad)
                pfade[sha] = pfad
            if sha is None and k["schluessel"] in bekannt:
                # Bild leer, zu gross, falscher Typ oder ausserhalb des Ordners:
                # nicht jede Minute neu versuchen.
                self._ungeeignet.add(k["schluessel"])
            zeilen.append({**k, "sha256": sha})
        antwort = await self._rufe(c, "POST", "/v1/karten",
                                   json={"v": VERSION, "zeilen": zeilen}) or {}
        geschickt = 0
        for sha in antwort.get("fehlen") or []:
            if geschickt >= BILDER_JE_RUNDE or sha not in pfade:
                continue
            typ = next((z["medientyp"] for z in zeilen if z["sha256"] == sha), None)
            daten = await asyncio.to_thread(pfade[sha].read_bytes)
            try:
                await self._rufe(c, "PUT", f"/v1/bild/{sha}", content=daten,
                                 headers={"Content-Type": typ or "image/jpeg"})
            except ZentraleFehler as e:
                if e.status is None or not 400 <= e.status < 500 or e.status == 408:
                    raise
                # Die Annahme lehnt genau dieses Bild ab: ueberspringen und
                # merken, die uebrigen gehen weiter.
                log.warning("Zentrale: Bild %s abgelehnt (HTTP %d), uebersprungen",
                            sha[:12], e.status)
                self._ungeeignet.update(z["schluessel"] for z in zeilen if z["sha256"] == sha)
                continue
            geschickt += 1
        # Karten ohne Bild, die schon bekannt sind, zaehlen nicht als offen.
        return len(offen) > len(teil) or geschickt >= BILDER_JE_RUNDE

    # ------------------------------------------------------------------- Ablauf
    async def runde(self, jetzt: float | None = None) -> None:
        """Ein Durchgang: Meldung immer, der Rest nach Faelligkeit."""
        jetzt = time.time() if jetzt is None else jetzt
        if not self._faellig.get("abgleich"):
            self._faellig["abgleich"] = float(self._zustand.get("abgleich_at") or 0.0) + ABGLEICH_S
        async with self._client() as c, httpx.AsyncClient(
                timeout=20.0, transport=self._lokal_transport) as lc:
            await self.meldung(c, lc)
            for teil, abstand, aufruf in (
                ("qso", QSO_S, lambda: self.qsos(c)),
                ("karten", KARTEN_S, lambda: self.karten(c)),
                ("profil", PROFIL_S, lambda: self.profil(c, lc)),
                ("abgleich", ABGLEICH_S, lambda: self.abgleich(c)),
            ):
                if jetzt >= self._faellig.get(teil, 0.0):
                    weiter = await aufruf()
                    # Noch etwas offen: in der naechsten Runde weiter, nicht erst
                    # nach dem vollen Abstand.
                    self._faellig[teil] = jetzt + (MELDUNG_S if weiter else abstand)
        self.letzte_ok = aufbereitung.stand_jetzt()

    def neues_qso(self) -> None:
        """Nach einem geloggten QSO: in der naechsten Runde schicken."""
        self._faellig["qso"] = 0.0

    async def lauf(self) -> None:
        await asyncio.sleep(15.0)
        while True:
            pause = MELDUNG_S
            try:
                await self.runde()
                if self.letzter_fehler:
                    log.info("Zentrale: wieder erreichbar")
                self._fehler_in_folge = 0
                self.letzter_fehler = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._fehler_in_folge += 1
                text = str(exc) or type(exc).__name__
                warte = getattr(exc, "warte_s", None)
                pause = warte if warte is not None else min(
                    PAUSE_MAX_S, MELDUNG_S * 2 ** (self._fehler_in_folge - 1))
                # Offline ist der Normalfall einer Station; nur der erste
                # Fehler einer Folge und dauerhafte Fehler stehen als Warnung im Log.
                if self._fehler_in_folge == 1 or getattr(exc, "dauerhaft", False):
                    log.warning("Zentrale: %s (naechster Versuch in %.0f s)", text, pause)
                else:
                    log.debug("Zentrale: %s", text)
                self.letzter_fehler = text
            await asyncio.sleep(pause)
