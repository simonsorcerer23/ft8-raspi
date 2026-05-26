"""Marinefunker-Mitglieder-Lookup (MF-Dipl.Such-Abhakliste).

Liest backend/ft8_appliance/data/marinefunker.json (von
``scripts/import_marinefunker.py`` aus der DF7PM-PDF generiert) und
exposed eine O(1)-Lookup-Funktion.

Sebastian 2026-05-26 v0.9.0:
- Nur AKTIVE Mitglieder sind in der JSON (Austritt-Spalte leer)
- ⚓-Badge in Decodes, QSO-Log, ntfy-Pushes
- Snapshot-Pattern: Status zum QSO-Zeitpunkt einfrieren in qso.mf_member_at_qso

Edge cases:
- Compound calls (DL7PM/P, DL7PM/MM) → Suffix vor Lookup strippen
- Hash-resolved calls (``<DL7PM>``) → Brackets entfernen
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_DATA_PATH = Path(__file__).parent.parent / "data" / "marinefunker.json"


@dataclass(frozen=True, slots=True)
class MfMember:
    """Active Marinefunker member as read from the JSON."""
    call: str       # normalized callsign (uppercase, no suffix)
    mfnr: int       # Marinefunker-Mitgliedsnummer
    dok: str | None
    since: str | None  # Eintrittsdatum als String (z.B. "01.09.1977")


class MfLookup:
    """In-memory lookup for active Marinefunker members.

    Loaded once at startup from data/marinefunker.json (~30 KB, ~366
    members). Lookup is O(1) via dict.
    """

    def __init__(self, data: dict[str, dict] | None = None):
        if data is None:
            data = self._load()
        self._members: dict[str, MfMember] = {
            call: MfMember(
                call=call,
                mfnr=int(entry["mfnr"]),
                dok=entry.get("dok"),
                since=entry.get("since"),
            )
            for call, entry in data.items()
        }

    @staticmethod
    def _load() -> dict[str, dict]:
        if not _DATA_PATH.exists():
            return {}
        try:
            return json.loads(_DATA_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    @staticmethod
    def normalize(call: str) -> str:
        """Strip suffixes (/P, /MM, /M, /QRP) and brackets (<DL7PM>),
        uppercase. Compound prefixes (DL/W1AW) → take the base part
        after the slash (W1AW) since that's the actual licensed call.
        """
        c = call.upper().strip()
        # Brackets von hash-resolved calls
        if c.startswith("<") and c.endswith(">"):
            c = c[1:-1]
        # Suffix-Strip: alles nach erstem "/" — aber NUR wenn die Basis-
        # länge passt (DL/W1AW → /W1AW = base, W1AW/P → W1AW = base)
        if "/" in c:
            parts = c.split("/")
            # Längstes valides Stück nehmen (= base call)
            parts.sort(key=len, reverse=True)
            c = parts[0]
        return c

    def lookup(self, call: str) -> MfMember | None:
        """Returns MfMember if call is an active Marinefunker, else None."""
        if not call:
            return None
        return self._members.get(self.normalize(call))

    def __len__(self) -> int:
        return len(self._members)

    def __contains__(self, call: str) -> bool:
        return self.lookup(call) is not None


# Lazy singleton — Backend importiert via get_mf_lookup()
_singleton: MfLookup | None = None


def get_mf_lookup() -> MfLookup:
    global _singleton
    if _singleton is None:
        _singleton = MfLookup()
    return _singleton
