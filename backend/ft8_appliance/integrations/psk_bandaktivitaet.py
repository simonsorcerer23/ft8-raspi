"""PSK Reporter: Aktivitaet je Band im eigenen Locator-Feld (seit 2026-11-12).

Der Autopilot zaehlt Decodes nur auf dem Band, auf dem das Rig gerade steht.
Fuer jedes andere erlaubte Band hatte er nur die Tageszeit-Prior und wechselte
deshalb erst, wenn das eigene Band einschlief — nie, weil ein anderes besser
lief. ``psk-freq.pl`` liefert, was in den letzten 5 Minuten im 4-stelligen
Feld gemeldet wurde, je 10-kHz-Bucket eine Zeile::

    21070000 499 61 0 2
    # frequency score #spots #tx #rx

Eine Abfrage je 5 Minuten; dichter wird das Fenster auf der Gegenseite nicht.
"""

from __future__ import annotations

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
