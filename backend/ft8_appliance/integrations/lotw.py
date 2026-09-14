"""LoTW-Upload ueber die TQSL-Kommandozeile.

Anders als bei eQSL oder ClubLog gibt es hier keinen Endpunkt, an den man
Zugangsdaten schickt: LoTW nimmt nur Logdateien an, die mit dem
Callsign-Zertifikat des Funkamateurs signiert sind. Das Signieren kann
nur TQSL, und TQSL laeuft lokal. Deshalb ruft dieses Modul ein fremdes
Programm auf, statt eine Schnittstelle zu bedienen.

Drei Dinge, die den Entwurf bestimmen (Quellen: lotw.arrl.org/lotw-help/
cmdline und developer-submit-qsos, gelesen 2026-09-14):

``xvfb-run``: Das Debian-Paket bringt nur die GTK-Fassung mit, die ohne
DISPLAY nicht startet — auch im Stapelbetrieb nicht. Ein virtuelles
Display kostet nichts und ist der uebliche Weg.

Exit-Codes sind nicht binaer. 0 heisst Erfolg, **8 heisst "alles waren
Duplikate" und 9 "teils Duplikate, Rest hochgeladen"** — beides ist kein
Fehler, sondern der Normalfall bei einem erneuten Lauf. Wer nur auf 0
prueft, haelt gelungene Uploads fuer gescheitert.

Duplikate erkennt TQSL anhand einer lokalen Datenbank dieses Rechners,
nicht per Serverabfrage. Wir fuehren trotzdem eigene Flags: Nach einem
Wechsel der SD-Karte waere TQSLs Gedaechtnis leer, unseres nicht.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..db.models import Qso

log = logging.getLogger(__name__)

# 0 = alles signiert und hochgeladen, 8 = nichts uebrig (alles Duplikate),
# 9 = ein Teil war Duplikat, der Rest ging raus.
ERFOLG = frozenset({0, 8, 9})
# Endgueltig: von LoTW zurueckgewiesen, TQSL-Fehler, Bibliotheksfehler,
# Syntaxfehler, Eingabedatei unlesbar. Erneut zu versuchen bringt nichts.
HART = frozenset({2, 4, 5, 6, 7, 10})
_STATUS = re.compile(r"Final Status:\s*(.+?)\s*\((\d+)\)", re.I)


class LotwError(RuntimeError):
    def __init__(self, text: str, *, hart: bool = False, code: int | None = None) -> None:
        super().__init__(text)
        self.hart = hart
        self.code = code


@dataclass(frozen=True, slots=True)
class LotwErgebnis:
    code: int
    meldung: str
    gesamt: int

    @property
    def alles_duplikate(self) -> bool:
        return self.code == 8


def _feld(name: str, wert: object) -> str:
    if wert is None:
        return ""
    s = str(wert).strip()
    return f"<{name}:{len(s)}>{s}" if s else ""


def baue_adif(qsos: list[Qso]) -> str:
    """ADIF fuer TQSL. Pflicht laut ARRL: CALL, MODE, QSO_DATE, TIME_ON
    sowie BAND oder FREQ.

    Die MY_-Felder bleiben bewusst weg: TQSL nimmt Standortangaben aus
    der Station Location und prueft vorhandene MY_-Felder dagegen. Wer
    sie mitschickt, handelt sich nur Konsistenzfehler ein.
    """
    zeilen = [
        "ADIF-Export der FT8-Appliance fuer LoTW",
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
            _feld("time_on", f"{ts:%H%M%S}"),
            _feld("band", (q.band or "").strip().lower()),
            _feld("mode", q.mode),
        ]
        zeilen.append(" ".join(t for t in teile if t) + " <eor>")
    return "\n".join(zeilen) + "\n"


def lies_status(stderr: str, code: int) -> tuple[str, int]:
    """Die letzte Statuszeile von TQSL auswerten.

    TQSL schreibt im Stapelbetrieb eine Zeile der Form
    ``HH:MM:SS Final Status: Beschreibung (Code)``. Die ARRL dokumentiert
    diese Zahl als "Code", ohne zuzusichern, dass sie auch der
    Prozess-Exitcode ist — deshalb hat die Statuszeile hier Vorrang, und
    der Exitcode ist nur der Rueckfall.
    """
    treffer = _STATUS.search(stderr)
    if treffer:
        return treffer.group(1), int(treffer.group(2))
    return (stderr.strip().splitlines() or ["ohne Meldung"])[-1][:200], code


async def upload(
    qsos: list[Qso], *, station_location: str, cert_password: str | None = None,
    timeout: float = 300.0,
) -> LotwErgebnis:
    """Eine Charge signieren und hochladen. Leere Liste = nichts tun."""
    if not qsos:
        return LotwErgebnis(0, "nichts zu tun", 0)
    if not shutil.which("tqsl"):
        raise LotwError("tqsl ist nicht installiert", hart=True)

    with tempfile.TemporaryDirectory(prefix="lotw-") as tmp:
        pfad = Path(tmp) / "upload.adi"
        pfad.write_text(baue_adif(qsos), encoding="utf-8")
        befehl = ["tqsl", "-d", "-a", "compliant", "-l", station_location,
                  "-u", "-x", str(pfad)]
        if cert_password:
            # Sichtbar in der Prozessliste. Ein Zertifikat ohne Passphrase
            # zu importieren ist auf einer Einzelplatz-Station sauberer;
            # siehe docs/lotw.md.
            befehl += ["-p", cert_password]
        if shutil.which("xvfb-run"):
            befehl = ["xvfb-run", "-a", *befehl]
        try:
            proc = await asyncio.create_subprocess_exec(
                *befehl, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            raise LotwError(f"tqsl antwortete {timeout:.0f}s nicht") from None
        except OSError as exc:
            raise LotwError(f"tqsl liess sich nicht starten: {exc}", hart=True) from exc

    meldung, code = lies_status(err.decode("utf-8", "replace"), proc.returncode or 0)
    if code in ERFOLG:
        return LotwErgebnis(code, meldung, len(qsos))
    raise LotwError(meldung, hart=code in HART, code=code)
