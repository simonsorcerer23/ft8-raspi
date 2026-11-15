"""PSK Reporter: welche Sender Empfaenger im eigenen Feld gerade hoeren.

Quelle der Rotor-Nachfuehrung (seit 2026-11-15). Abgefragt wird
``retrieve.pskreporter.info/query`` mit ``receiverCallsign=<Feld>`` und
``modify=grid``. Laut pskdev.html werden die Rufzeichen-Parameter damit
"als Locator gelesen"; dass das Feld dann die *Empfaenger* filtert, steht
dort nicht. Am 15.11.2026 einmal von Hand geprueft: JN58, 15 m, letzte
15 Minuten — 569 Meldungen, alle mit ``receiverLocator`` JN58..., jede mit
``senderLocator``.

Hoeflichkeit laut pskdev.html: nicht oefter als alle fuenf Minuten
abfragen. Die Nachfuehrung fragt hoechstens alle zehn Minuten.
"""

from __future__ import annotations

import re

from ..util.bandplan import band_grenzen_hz
from .base import Integration

PSK_RETRIEVE_URL = "https://retrieve.pskreporter.info/query"
_REPORT = re.compile(r"<receptionReport\s([^>]*)/>")
_ATTR = re.compile(r'(\w+)="([^"]*)"')


def parse_sender(xml: str) -> list[tuple[str, str]]:
    """(Rufzeichen, Locator) jedes gemeldeten Senders; ohne Locator entfaellt er."""
    sender: list[tuple[str, str]] = []
    for m in _REPORT.finditer(xml or ""):
        a = dict(_ATTR.findall(m.group(1)))
        call = (a.get("senderCallsign") or "").strip().upper()
        grid = (a.get("senderLocator") or "").strip()
        if call and len(grid) >= 4:
            sender.append((call, grid))
    return sender


class PskRichtung(Integration):
    name = "psk_richtung"

    def __init__(self, *, timeout: float = 30.0, cache_ttl_s: float = 300.0) -> None:
        super().__init__(enabled=True, base_url=None, timeout=timeout, cache_ttl_s=cache_ttl_s)

    async def sender_im_feld(
        self, locator: str, band: str, *, fenster_s: int = 900,
    ) -> list[tuple[str, str]] | None:
        """Sender, die Empfaenger im 4-stelligen Feld von *locator* gehoert haben.

        None, wenn nichts zu holen war (Netz, Antwort ohne Meldungen) — der
        Aufrufer entscheidet dann nicht neu.
        """
        feld = (locator or "")[:4].upper()
        grenzen = band_grenzen_hz(band)
        if len(feld) < 4 or grenzen is None:
            return None
        key = f"{feld}:{band}:{fenster_s}"
        cached = await self.cache.get(key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        params = {
            "receiverCallsign": feld,
            "modify": "grid",
            "flowStartSeconds": -abs(int(fenster_s)),
            "frange": f"{grenzen[0]}-{grenzen[1]}",
            "rronly": 1,
            "noactive": 1,
            "rptlimit": 5000,
        }
        try:
            r = await self._get(PSK_RETRIEVE_URL, params=params)
        except Exception:
            return None
        sender = parse_sender(r.text)
        if not sender:
            return None
        await self.cache.set(key, sender)
        return sender
