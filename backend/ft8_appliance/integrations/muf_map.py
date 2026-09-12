"""MUF-Weltkarte von prop.kc2g.com als Kartenebene.

Die Station fragt dort bereits Punkt-zu-Punkt-Vorhersagen ab (``PathPrediction``,
zwanzig Zielfelder alle fuenfzehn Minuten). Aus zwanzig Strecken laesst sich
aber keine Flaeche einfaerben — fuer eine Kartenebene braucht es ein Raster.

Dasselbe Projekt rendert stuendlich eine Weltkarte und liefert sie als SVG
aus. Darin steckt die eigentliche Flaeche als eingebettetes PNG in
gleichabstaendiger Zylinderprojektion (2732 x 1366, Seitenverhaeltnis exakt
2:1). Das laesst sich ohne Umprojektion als Bildebene ueber die Kartenecken
legen; wir schneiden es aus dem SVG heraus und liefern nur das Bild aus.

Geladen wird **traege**: erst wenn jemand die Ebene einschaltet, und dann
hoechstens einmal je Stunde — der Dienst wird kostenlos betrieben, und die
Vorhersage wird ohnehin nur stuendlich gerechnet. Scheitert ein Abruf, bleibt
das alte Bild gueltig und sein Alter wird im Metadaten-Endpunkt sichtbar; ein
neuer Versuch folgt fruehestens nach ``_FEHLER_SPERRE_S``, damit ein Ausfall
dort nicht in Dauerabrufe von hier umschlaegt.
"""

from __future__ import annotations

import base64
import logging
import re
import struct
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_QUELLE = "https://prop.kc2g.com/renders/current/mufd-normal-now.svg"
_UA = "ft8-raspi (Amateurfunk, DK9XR)"
_TTL_S = 3600.0
"""Die Vorhersage wird stuendlich gerechnet — oefter zu fragen bringt dieselben
Zahlen zurueck und belastet nur den Dienst."""
_FEHLER_SPERRE_S = 300.0
"""Nach einem Fehlschlag so lange nicht erneut versuchen. Ohne die Sperre
wuerde jeder Kartenaufruf waehrend einer Stoerung einen neuen Versuch
ausloesen."""
_TIMEOUT_S = 25.0
_MAX_BYTES = 4 * 1024 * 1024
"""Das SVG liegt bei rund 380 KB. Die Schranke verhindert, dass eine kaputte
Antwort den Arbeitsspeicher fuellt."""

_PNG_IM_SVG = re.compile(rb'href="data:image/png;base64,([^"]+)"')
"""Absichtlich ``[^"]+`` statt einer Base64-Zeichenklasse: Der Renderer setzt
ein Leerzeichen hinter das Komma und darf den Block auch umbrechen. Eine enge
Klasse trifft dann nichts, und zwar *still* — der erste Entwurf hier ist genau
daran gescheitert, waehrend die Tests gruen blieben, weil ihr SVG keinen
Zwischenraum enthielt. Der Whitespace faellt vor dem Dekodieren weg."""


@dataclass(frozen=True)
class MufKarte:
    """Das ausgelieferte Bild samt Herkunftsangabe."""

    png: bytes
    geholt_at: float          # time.time() des erfolgreichen Abrufs
    breite: int
    hoehe: int

    @property
    def alter_s(self) -> float:
        return max(0.0, time.time() - self.geholt_at)


class MufKartenCache:
    """Haelt die zuletzt geholte Karte, auf Platte und im Speicher.

    Die Datei ueberlebt einen Neustart. Das ist der Unterschied zwischen
    „nach jedem Update erst mal keine Karte" und „die von vorhin, mit
    sichtbarem Alter".
    """

    def __init__(self, pfad: Path) -> None:
        self._pfad = pfad
        self._karte: MufKarte | None = None
        self._letzter_fehler_at = 0.0

    # -- oeffentlich --------------------------------------------------------
    def hole(self, *, erzwingen: bool = False) -> MufKarte | None:
        """Aktuelle Karte, notfalls die alte. ``None`` nur ohne jede Karte."""
        if self._karte is None:
            self._karte = self._von_platte()
        frisch = self._karte is not None and self._karte.alter_s < _TTL_S
        if frisch and not erzwingen:
            return self._karte
        if not erzwingen and time.time() - self._letzter_fehler_at < _FEHLER_SPERRE_S:
            return self._karte          # Sperrfrist laeuft — alte Karte reicht
        neu = self._abrufen()
        if neu is None:
            self._letzter_fehler_at = time.time()
            return self._karte          # kann selbst None sein
        self._karte = neu
        self._auf_platte(neu)
        return neu

    # -- intern -------------------------------------------------------------
    def _abrufen(self) -> MufKarte | None:
        try:
            req = urllib.request.Request(_QUELLE, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:  # noqa: S310
                roh = r.read(_MAX_BYTES + 1)
            if len(roh) > _MAX_BYTES:
                log.warning("MUF-Karte: Antwort groesser als %d Bytes — verworfen", _MAX_BYTES)
                return None
            png = _png_aus_svg(roh)
            if png is None:
                log.warning("MUF-Karte: kein eingebettetes PNG im SVG gefunden")
                return None
            masse = _png_masse(png)
            if masse is None:
                log.warning("MUF-Karte: eingebettete Daten sind kein PNG")
                return None
            breite, hoehe = masse
            if hoehe <= 0 or abs(breite / hoehe - 2.0) > 0.01:
                # Ohne 2:1 ist es keine gleichabstaendige Weltkarte, und die
                # Bildebene laege falsch ueber der Karte. Lieber nichts zeigen.
                log.warning("MUF-Karte: Seitenverhaeltnis %d x %d ist nicht 2:1", breite, hoehe)
                return None
            return MufKarte(png=png, geholt_at=time.time(), breite=breite, hoehe=hoehe)
        except Exception as exc:
            log.info("MUF-Karte nicht erreichbar: %s", exc)
            return None

    def _von_platte(self) -> MufKarte | None:
        try:
            if not self._pfad.exists():
                return None
            png = self._pfad.read_bytes()
            masse = _png_masse(png)
            if masse is None:
                return None
            return MufKarte(png=png, geholt_at=self._pfad.stat().st_mtime,
                            breite=masse[0], hoehe=masse[1])
        except Exception as exc:
            log.debug("MUF-Karte: Cache nicht lesbar (%s)", exc)
            return None

    def _auf_platte(self, karte: MufKarte) -> None:
        try:
            self._pfad.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._pfad.with_suffix(".tmp")
            tmp.write_bytes(karte.png)
            tmp.replace(self._pfad)     # atomar: ein Absturz laesst keine halbe Datei
        except Exception as exc:
            log.debug("MUF-Karte: Cache nicht schreibbar (%s)", exc)


def _png_aus_svg(svg: bytes) -> bytes | None:
    """Das eingebettete Rasterbild aus dem gerenderten Plot herausloesen."""
    m = _PNG_IM_SVG.search(svg)
    if m is None:
        return None
    try:
        # validate=False ist hier nicht genug: b64decode wirft bei Whitespace
        # zwar nicht, verschluckt ihn aber nur am Rand zuverlaessig.
        return base64.b64decode(re.sub(rb"\s+", b"", m.group(1)))
    except Exception:
        return None


def _png_masse(png: bytes) -> tuple[int, int] | None:
    """Breite und Hoehe aus dem PNG-Kopf, ohne Bildbibliothek."""
    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR":
        return None
    breite, hoehe = struct.unpack(">II", png[16:24])
    return int(breite), int(hoehe)
