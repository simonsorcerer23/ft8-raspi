"""HamQTH.com API client (free QRZ alternative).

Docs: https://www.hamqth.com/developers.php
Same two-step flow as QRZ: login -> get session_id, then lookup callsign.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree as ET

from .base import Integration

HAMQTH_URL = "https://www.hamqth.com/xml.php"
NS = "{https://www.hamqth.com}"


@dataclass(frozen=True, slots=True)
class HamQthRecord:
    callsign: str
    name: str | None
    grid: str | None
    country: str | None


class HamQthClient(Integration):
    name = "hamqth"

    def __init__(
        self,
        user: str | None = None,
        password: str | None = None,
        *,
        enabled: bool = True,
        timeout: float = 5.0,
        cache_ttl_s: float = 3600.0,
    ) -> None:
        super().__init__(
            enabled=enabled and bool(user) and bool(password),
            base_url=None,  # full URL passed in each call (avoids httpx slash-append)
            timeout=timeout,
            cache_ttl_s=cache_ttl_s,
        )
        self._user = user
        self._password = password
        self._session_id: str | None = None

    async def session_login(self) -> str:
        if not self.enabled:
            raise RuntimeError("HamQTH integration not enabled / no credentials")
        r = await self._get(
            HAMQTH_URL,
            params={"u": self._user, "p": self._password},
        )
        root = ET.fromstring(r.text)
        node = root.find(f"{NS}session/{NS}session_id")
        if node is None or not node.text:
            raise RuntimeError("HamQTH login: no session_id")
        self._session_id = node.text
        return self._session_id

    async def callsign(self, call: str) -> HamQthRecord | None:
        if not self.enabled:
            return None
        call = call.upper().strip()
        cached = await self.cache.get(call)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        if self._session_id is None:
            try:
                await self.session_login()
            except Exception:
                return None
        try:
            r = await self._get(
                HAMQTH_URL,
                params={"id": self._session_id, "callsign": call, "prg": "ft8-appliance"},
            )
        except Exception:
            return None

        try:
            root = ET.fromstring(r.text)
        except ET.ParseError:
            return None

        sr = root.find(f"{NS}search")
        if sr is None:
            return None

        def f(tag: str) -> str | None:
            node = sr.find(f"{NS}{tag}")
            return node.text if node is not None else None

        rec = HamQthRecord(
            callsign=f("callsign") or call,
            name=f("nick") or f("adr_name"),
            grid=f("grid"),
            country=f("country"),
        )
        await self.cache.set(call, rec)
        return rec
