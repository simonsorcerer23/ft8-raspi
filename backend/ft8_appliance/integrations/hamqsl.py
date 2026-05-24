"""hamqsl.com Solar Indices client.

Returns SFI / A / K / sunspots / x-ray / aurora etc., updated every 3h
by N0NBH. We cache for 30 min by default to keep API usage trivial.

Reference: https://www.hamqsl.com/solarxml.php
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree as ET

from .base import Integration

HAMQSL_URL = "https://www.hamqsl.com/solarxml.php"


@dataclass(frozen=True, slots=True)
class SolarData:
    sfi: int | None  # 10.7 cm solar flux
    a_index: int | None
    k_index: int | None
    sunspots: int | None
    x_ray: str | None
    aurora: int | None
    updated: str | None


class HamQslClient(Integration):
    name = "hamqsl"

    def __init__(
        self,
        *,
        enabled: bool = True,
        timeout: float = 5.0,
        cache_ttl_s: float = 1800.0,
    ) -> None:
        super().__init__(
            enabled=enabled,
            base_url=None,  # full URL each call
            timeout=timeout,
            cache_ttl_s=cache_ttl_s,
        )

    async def solar(self) -> SolarData | None:
        if not self.enabled:
            return None
        cached = await self.cache.get("solar")
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        try:
            r = await self._get(HAMQSL_URL)
        except Exception:
            stale, _ = await self.cache.get_stale_ok("solar")
            return stale  # type: ignore[no-any-return]
        sd = _parse(r.text)
        if sd is not None:
            await self.cache.set("solar", sd)
        return sd


def _parse(xml_text: str) -> SolarData | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    sdata = root.find("solardata")
    if sdata is None:
        return None

    def text(tag: str) -> str | None:
        n = sdata.find(tag)
        return n.text.strip() if n is not None and n.text else None

    def i(tag: str) -> int | None:
        t = text(tag)
        try:
            return int(t) if t is not None else None
        except ValueError:
            return None

    return SolarData(
        sfi=i("solarflux"),
        a_index=i("aindex"),
        k_index=i("kindex"),
        sunspots=i("sunspots"),
        x_ray=text("xray"),
        aurora=i("aurora"),
        updated=text("updated"),
    )
