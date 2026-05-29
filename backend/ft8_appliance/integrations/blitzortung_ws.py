"""WebSocket-Consumer fuer den oeffentlichen Blitzortung.org Live-Stream.

Endpunkt: ``wss://ws1.blitzortung.org/`` (Round-Robin gibt's auch ws7/ws8;
ws3 ist 2026-05-27 raus weil das Server-Cert nicht fuer den Hostname
ausgestellt ist → TLS-Hostname-Mismatch).
Nach Connect sendet der Client ``{"a":111}`` als Subscribe-Token; der
Server schickt fortlaufend Text-Frames mit LZW-komprimiertem JSON
(je ein Strike-Event pro Frame).

Decoder ist 1:1-Aequivalent der canonical-JS-Variante die map.blitzortung
.org im Browser nutzt — Codepoints ≥256 werden als LZW-Codes interpretiert,
Codepoints <256 als Literale.

Strike-JSON-Schema (Stand 2026):
    {
      "time": <int, ns since epoch>,
      "lat": <float deg>, "lon": <float deg>, "alt": <float m>,
      "pol": <int>, "mds": <int>, "mcg": <int>,
      "status": <int>, "region": <int>,
      "sig_num": <int>, "delay": <float>,
      "lat_c": <float>, "lon_c": <float>,
      "sig": [...]
    }

Wir nehmen nur ``time``, ``lat``, ``lon`` — alles andere ist fuer unsere
Push-Warnung irrelevant.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import websockets

from .blitzortung import Strike

log = logging.getLogger(__name__)

# Default-Round-Robin der oeffentlichen Server. Bei Connect-Fail
# wechseln wir den naechsten an.
DEFAULT_WS_HOSTS = ("ws1.blitzortung.org",
                    "ws7.blitzortung.org", "ws8.blitzortung.org")
SUBSCRIBE_FRAME = '{"a":111}'


def lzw_decode(s: str) -> str:
    """LZW-Variante wie sie der Blitzortung-Browser-Client nutzt.

    Eingabe: Unicode-String wo jeder Codepoint ≥256 als LZW-Code in das
    aufgebaute Dictionary verweist. Ausgabe: dekomprimierter JSON-String.

    Algorithmus 1:1 aus der canonical JS-Funktion (gemeinfrei seit ~2014).
    """
    if not s:
        return ""
    data = list(s)
    dictionary: dict[int, str] = {}
    c = data[0]
    f = c
    out = [c]
    next_code = 256
    for ch in data[1:]:
        i = ord(ch)
        if i < 256:
            a = ch
        elif i in dictionary:
            a = dictionary[i]
        else:
            # Edge-case: Code = next free slot, decoded mit f + f[0]
            a = f + c
        out.append(a)
        c = a[0]
        dictionary[next_code] = f + c
        next_code += 1
        f = a
    return "".join(out)


def parse_strike(raw_json: str) -> Strike | None:
    """Parse ein decodes Strike-JSON; ``None`` bei Schemafehler."""
    try:
        d = json.loads(raw_json)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    try:
        # "time" ist nanoseconds since unix-epoch (Blitzortung-Konvention).
        # Wir wandeln in datetime — Sub-Sekunden-Aufloesung ist fuer
        # 30-min-Retention egal.
        ts_ns = int(d["time"])
        ts = datetime.fromtimestamp(ts_ns / 1e9, tz=UTC)
        lat = float(d["lat"])
        lon = float(d["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    return Strike(ts=ts, lat=lat, lon=lon)


async def stream_strikes(
    *,
    hosts: tuple[str, ...] = DEFAULT_WS_HOSTS,
    backoff_base_s: float = 5.0,
    backoff_cap_s: float = 120.0,
    good_session_s: float = 60.0,
) -> AsyncIterator[Strike]:
    """Async-Generator der Strikes vom Live-WS liefert.

    Robust gegen Disconnects: bei Connection-Drop wird der naechste
    Host probiert (Round-Robin). Reconnect mit **exponential Backoff**
    (analog DX-Cluster, Sebastian 2026-05-29): nach einem sofortigen
    Fail / Flapping waechst die Pause ``backoff_base_s`` → ×2 → bis
    ``backoff_cap_s``, damit wir blitzortung.org bei Ausfall nicht
    alle 30 s endlos hammern. Nach einer ECHTEN Session (laenger als
    ``good_session_s`` verbunden) wird der Backoff zurueckgesetzt —
    ein transienter Drop nach Stunden soll schnell reconnecten.

    Connect+Subscribe-Pattern:
        1. websockets.connect(url, ping_interval=30, ping_timeout=10)
        2. await ws.send('{"a":111}')
        3. async for frame in ws: try LZW-decode → parse JSON → yield
    """
    host_idx = 0
    backoff = backoff_base_s
    while True:
        host = hosts[host_idx % len(hosts)]
        url = f"wss://{host}/"
        connected_at: float | None = None
        try:
            log.info("blitzortung: connecting to %s", url)
            async with websockets.connect(
                url,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
                # Server schickt grosse Frames bei aktiven Fronten —
                # Default 1 MiB reicht locker, expliziter zur Klarheit.
                max_size=2 * 1024 * 1024,
            ) as ws:
                await ws.send(SUBSCRIBE_FRAME)
                connected_at = time.monotonic()
                log.info("blitzortung: subscribed, streaming strikes")
                async for frame in ws:
                    if not isinstance(frame, str):
                        # Defensiv: Server koennte theoretisch auch Binary
                        # schicken — wir interpretieren das nicht.
                        continue
                    try:
                        decoded = lzw_decode(frame)
                    except Exception as exc:
                        log.debug("blitzortung: LZW-decode failed: %s", exc)
                        continue
                    strike = parse_strike(decoded)
                    if strike is not None:
                        yield strike
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("blitzortung: WS error on %s (%s) — rotating host",
                        host, exc)
        host_idx += 1
        # Backoff-Bewertung: stand die Session lang genug (echter Stream,
        # dann transienter Drop) → zurueck auf Base, schnell reconnecten.
        # Sonst (Sofort-Fail / Flapping / Server lehnt ab) → exponential
        # wachsen lassen bis Cap, damit wir den Dienst nicht hammern.
        if connected_at is not None and (time.monotonic() - connected_at) > good_session_s:
            backoff = backoff_base_s
        else:
            backoff = min(backoff * 2, backoff_cap_s)
        try:
            await asyncio.sleep(backoff)
        except asyncio.CancelledError:
            raise
