"""QRZ.com XML-API client.

Reference: https://www.qrz.com/XML/current_spec.html

Two-step usage:
1. ``session_login()`` — POST credentials, get a session key (~24h valid)
2. ``callsign(call)`` — GET callsign lookup with the session key

We cache per callsign for the configured TTL (default 1 h) so a repeated
lookup of the same call doesn't hit QRZ.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from .base import Integration

log = logging.getLogger(__name__)

QRZ_URL = "https://xmldata.qrz.com/xml/current/"
NS = "{http://xmldata.qrz.com}"


@dataclass(frozen=True, slots=True)
class QrzRecord:
    callsign: str
    first_name: str | None
    last_name: str | None
    grid: str | None
    country: str | None
    image_url: str | None


class QrzClient(Integration):
    name = "qrz"

    def __init__(
        self,
        user: str | None,
        password: str | None,
        *,
        enabled: bool = True,
        timeout: float = 5.0,
        cache_ttl_s: float = 3600.0,
    ) -> None:
        super().__init__(
            enabled=enabled and bool(user) and bool(password),
            base_url=QRZ_URL,
            timeout=timeout,
            cache_ttl_s=cache_ttl_s,
        )
        self._user = user
        self._password = password
        self._session_key: str | None = None

    async def session_login(self) -> str:
        if not self.enabled:
            raise RuntimeError("QRZ integration not enabled / no credentials")
        r = await self._get(
            "",
            params={"username": self._user, "password": self._password, "agent": "ft8-appliance/0.1"},
        )
        key = _parse_session_key(r.text)
        if key is None:
            raise RuntimeError("QRZ session login failed: no Key in response")
        self._session_key = key
        return key

    async def callsign(self, call: str) -> QrzRecord | None:
        if not self.enabled:
            return None
        call = call.upper().strip()
        cached = await self.cache.get(call)
        if cached is not None:
            return cached  # type: ignore[no-any-return]

        if self._session_key is None:
            try:
                await self.session_login()
            except Exception:
                return None

        try:
            r = await self._get("", params={"s": self._session_key, "callsign": call})
        except Exception:
            return None

        rec = _parse_callsign(r.text, call)
        if rec is not None:
            await self.cache.set(call, rec)
        return rec


# ---------------------------------------------------------------------------
def _parse_session_key(xml_text: str) -> str | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    session = root.find(f"{NS}Session")
    if session is None:
        return None
    key_node = session.find(f"{NS}Key")
    return key_node.text if key_node is not None else None


def _parse_callsign(xml_text: str, call: str) -> QrzRecord | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    cs = root.find(f"{NS}Callsign")
    if cs is None:
        return None

    def f(tag: str) -> str | None:
        node = cs.find(f"{NS}{tag}")
        return node.text if node is not None else None

    return QrzRecord(
        callsign=f("call") or call,
        first_name=f("fname"),
        last_name=f("name"),
        grid=f("grid"),
        country=f("country"),
        image_url=f("image"),
    )
