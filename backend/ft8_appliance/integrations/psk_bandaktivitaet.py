"""PSK Reporter: Aktivitaet je Band im eigenen Locator-Feld (seit 2026-11-12).

Der Autopilot zaehlt Decodes nur auf dem Band, auf dem das Rig gerade steht.
Fuer jedes andere erlaubte Band hatte er nur die Tageszeit-Prior und wechselte
deshalb erst, wenn das eigene Band einschlief — nie, weil ein anderes besser
lief. ``psk-freq.pl`` liefert, was in den letzten 5 Minuten im 4-stelligen
Feld gemeldet wurde, je 10-kHz-Bucket eine Zeile::

    21070000 499 61 0 2
    # frequency score #spots #tx #rx

Eine Abfrage je 5 Minuten; dichter wird das Fenster auf der Gegenseite nicht.

Seit 2026-11-14 zusaetzlich je Betriebsart: Die Zeilen sind auf 10 kHz
gerundete Sendefrequenzen. FT8 auf 21.074 MHz (Audio 0,2-3 kHz) faellt damit
in 21070000 und 21080000, FT4 auf 21.140 MHz in 21140000. Wo sich beide
dieselbe Zeile teilen (20 m, 17 m, 12 m, 80 m), ist keine Trennung moeglich.
Dafuer wird das Grossfeld (zwei Zeichen) abgefragt: Im Kleinfeld hoert oft
niemand FT4 mit, und null Meldungen hiessen dann nichts.
"""

from __future__ import annotations

import math
from typing import NamedTuple

from ..util.bandplan import band_from_freq_hz
from .base import Integration

PSK_FREQ_URL = "https://pskreporter.info/cgi-bin/psk-freq.pl"


class PskBandaktivitaet(Integration):
    name = "psk_bandaktivitaet"

    def __init__(self, *, timeout: float = 10.0, cache_ttl_s: float = 240.0) -> None:
        super().__init__(enabled=True, base_url=None, timeout=timeout, cache_ttl_s=cache_ttl_s)

    async def spots_je_band(self, locator: str) -> dict[str, int] | None:
        """Meldungen je Band im Feld von *locator*; None, wenn nichts zu holen war."""
        feld = (locator or "")[:4].upper()
        if len(feld) < 4:
            return None
        cached = await self.cache.get(feld)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        try:
            r = await self._get(PSK_FREQ_URL, params={"grid": feld})
        except Exception:
            return None
        spots = parse(r.text)
        if spots:
            await self.cache.set(feld, spots)
        return spots or None

    async def zeilen_im_grossfeld(self, locator: str) -> dict[int, int] | None:
        """Meldungen je 10-kHz-Zeile im Grossfeld von *locator*; None ohne Antwort."""
        feld = (locator or "")[:2].upper()
        if len(feld) < 2:
            return None
        cached = await self.cache.get(feld)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        try:
            r = await self._get(PSK_FREQ_URL, params={"grid": feld})
        except Exception:
            return None
        zeilen = parse_zeilen(r.text)
        if zeilen:
            await self.cache.set(feld, zeilen)
        return zeilen or None


class ModusMeldungen(NamedTuple):
    ft8: int
    ft4: int


def _zeilen_fuer_dial(dial_khz: int) -> set[int]:
    """10-kHz-Zeilen, in die ein Signal mit 0,2-3,0 kHz Audio auf diesem Dial faellt."""
    unten = math.floor((dial_khz + 0.2) / 10 + 0.5) * 10
    oben = math.floor((dial_khz + 3.0) / 10 + 0.5) * 10
    return {khz * 1000 for khz in range(unten, oben + 1, 10)}


def modus_meldungen(
    zeilen: dict[int, int], ft8_dial_khz: int, ft4_dial_khz: int,
) -> ModusMeldungen | None:
    """FT8- und FT4-Meldungen eines Bands; None, wenn beide dieselbe Zeile belegen."""
    z8, z4 = _zeilen_fuer_dial(ft8_dial_khz), _zeilen_fuer_dial(ft4_dial_khz)
    if z8 & z4:
        return None
    return ModusMeldungen(
        ft8=sum(zeilen.get(z, 0) for z in z8), ft4=sum(zeilen.get(z, 0) for z in z4),
    )


def parse_zeilen(text: str) -> dict[int, int]:
    """Spalte 3 (Meldungen) je 10-kHz-Zeile; Kommentare und Muell ueberspringen."""
    zeilen: dict[int, int] = {}
    for zeile in text.splitlines():
        teile = zeile.split()
        if len(teile) < 3 or teile[0].startswith("#"):
            continue
        try:
            hz, n = int(teile[0]), int(teile[2])
        except ValueError:
            continue
        if n > 0:
            zeilen[hz] = zeilen.get(hz, 0) + n
    return zeilen


def parse(text: str) -> dict[str, int]:
    """Spalte 3 (Meldungen) je Band aufsummieren; Kommentare und Muell ueberspringen."""
    spots: dict[str, int] = {}
    for zeile in text.splitlines():
        teile = zeile.split()
        if len(teile) < 3 or teile[0].startswith("#"):
            continue
        try:
            hz, n = int(teile[0]), int(teile[2])
        except ValueError:
            continue
        band = band_from_freq_hz(hz)
        if band is not None and n > 0:
            spots[band] = spots.get(band, 0) + n
    return spots
