"""Offline DXCC lookup via ``cty.dat`` (Country Files by AD1C).

Format documented at https://www.country-files.com/cty-dat-format/.
Each block describes one DXCC entity followed by a list of prefix
matchers terminated by ``;``. We only parse the bits we need:

* country name
* continent code
* primary prefix
* the list of prefix patterns (with optional ``=`` for exact-call match)

Lookup is O(longest prefix match), good enough for a few hundred queries
per second on a Pi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DxccEntity:
    name: str
    continent: str  # EU, NA, SA, AF, AS, OC, AN
    primary_prefix: str
    # Country-centre coordinates from cty.dat (field 4 = lat, field 5 = lon,
    # with positive lon = WEST per the cty.dat convention — we flip it on
    # parse so callers get normal math/leaflet positive=east). None if the
    # record came from a hand-built CtyDat without coords.
    lat: float | None = None
    lon: float | None = None


@dataclass(frozen=True, slots=True)
class DxccLookupResult:
    entity: DxccEntity
    matched_prefix: str
    exact_match: bool


# ---------------------------------------------------------------------------
class CtyDat:
    """Loaded cty.dat. Lookups are exact-call-first, then longest-prefix."""

    def __init__(self) -> None:
        # call/prefix -> entity
        self._exact: dict[str, DxccEntity] = {}
        self._prefixes: dict[str, DxccEntity] = {}

    @classmethod
    def load(cls, path: Path | str) -> CtyDat:
        inst = cls()
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        inst._parse(text)
        return inst

    @classmethod
    def from_string(cls, text: str) -> CtyDat:
        inst = cls()
        inst._parse(text)
        return inst

    # ------------------------------------------------------------------ parse
    def _parse(self, text: str) -> None:
        # Records are colon-separated, terminated by `;`. Re-join over
        # newlines first.
        records = text.replace("\n", "").split(";")
        for raw in records:
            raw = raw.strip()
            if not raw:
                continue
            fields = [f.strip() for f in raw.split(":")]
            if len(fields) < 9:
                continue
            name = fields[0]
            continent = fields[3]
            # cty.dat lat is positive=north (normal), lon is positive=WEST.
            # We invert lon so consumers get the standard "positive=east".
            try:
                lat = float(fields[4]) if fields[4] else None
            except ValueError:
                lat = None
            try:
                lon = -float(fields[5]) if fields[5] else None
            except ValueError:
                lon = None
            primary = fields[7].lstrip("*")
            patterns = fields[8].split(",")
            entity = DxccEntity(
                name=name, continent=continent, primary_prefix=primary,
                lat=lat, lon=lon,
            )
            for pat in patterns:
                pat = pat.strip()
                if not pat:
                    continue
                # Strip lat/long and qsl/qsl overrides — they appear in
                # parens or brackets. We only need the prefix letters.
                core = ""
                for ch in pat:
                    if ch.isalnum() or ch == "/":
                        core += ch
                    else:
                        break
                if not core:
                    continue
                if pat.startswith("="):
                    self._exact[core] = entity
                else:
                    self._prefixes[core] = entity

    # ------------------------------------------------------------------ lookup
    def lookup(self, callsign: str) -> DxccLookupResult | None:
        call = callsign.upper().strip()

        # 1. exact match
        if call in self._exact:
            return DxccLookupResult(self._exact[call], call, exact_match=True)

        # 2. longest prefix match against the prefix table
        # Try progressively shorter prefixes of the callsign.
        for length in range(len(call), 0, -1):
            prefix = call[:length]
            if prefix in self._prefixes:
                return DxccLookupResult(
                    self._prefixes[prefix], prefix, exact_match=False
                )
        return None

    def __len__(self) -> int:
        return len(self._prefixes) + len(self._exact)
