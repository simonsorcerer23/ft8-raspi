"""Tests for the integration clients.

HTTP-driven clients are tested with respx (httpx mocking). cty.dat is
tested against a small embedded snippet. Blitzortung is tested via
direct ingest of synthetic strikes — no network involved.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import respx
from httpx import Response

from ft8_appliance.integrations import (
    AsyncTTLCache,
    BlitzortungClient,
    CircuitBreaker,
    CircuitOpenError,
    CtyDat,
    HamQslClient,
    HamQthClient,
    NtfyClient,
    PskReporterClient,
    QrzClient,
    Strike,
    haversine_km,
)


# ============================================================================
# base — cache + circuit breaker
# ============================================================================
async def test_ttl_cache_get_set() -> None:
    c: AsyncTTLCache[str] = AsyncTTLCache(ttl_s=60.0)
    assert await c.get("k") is None
    await c.set("k", "v")
    assert await c.get("k") == "v"


async def test_ttl_cache_expires_but_stale_still_available() -> None:
    c: AsyncTTLCache[str] = AsyncTTLCache(ttl_s=0.01)
    await c.set("k", "v")
    await asyncio.sleep(0.05)
    # fresh get returns None
    assert await c.get("k") is None
    # but stale still readable for graceful-degrade
    stale, is_stale = await c.get_stale_ok("k")
    assert stale == "v" and is_stale is True


async def test_circuit_breaker_trips_then_recovers() -> None:
    cb = CircuitBreaker(failure_threshold=2, cool_off_s=0.05)

    async def boom() -> str:
        raise RuntimeError("nope")

    async def fine() -> str:
        return "ok"

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.run(boom)
    # circuit now open
    with pytest.raises(CircuitOpenError):
        await cb.run(fine)
    # wait for cool-off, half-open allows trial
    await asyncio.sleep(0.07)
    assert await cb.run(fine) == "ok"
    assert cb.state == "closed"


# ============================================================================
# cty.dat
# ============================================================================
CTY_DAT_FIXTURE = """\
Fed. Rep. of Germany:           14:  28:  EU:   51.00:   -10.00:    -1.0:  DL:
    DA,DB,DC,DD,DE,DF,DG,DH,DI,DJ,DK,DL,DM,DN,DO,DP,DQ,DR,Y2,Y3,Y4,Y5,Y6,Y7,Y8,Y9;
United States:                   5:   8:  NA:   37.53:    91.66:     5.0:  K:
    AA,AB,AC,AD,AE,AF,AG,AI,AJ,AK,K,KA,KB,KC,KD,KE,KF,KG,KH,KI,KJ,KK,KL,KM,KN,KO,KP,
    KQ,KR,KS,KT,KU,KV,KW,KX,KY,KZ,N,NA,NB,NC,ND,NE,NF,NG,NH,NI,NJ,NK,NL,NM,NN,NO,NP,
    NQ,NR,NS,NT,NU,NV,NW,NX,NY,NZ,W,WA,WB,WC,WD,WE,WF,WG,WH,WI,WJ,WK,WL,WM,WN,WO,WP,
    WQ,WR,WS,WT,WU,WV,WW,WX,WY,WZ;
"""


def test_cty_dat_lookup_dl_call() -> None:
    cty = CtyDat.from_string(CTY_DAT_FIXTURE)
    r = cty.lookup("DK9XR")
    assert r is not None
    assert r.entity.continent == "EU"
    assert r.entity.name.startswith("Fed")
    assert r.matched_prefix == "DK"
    assert r.exact_match is False


def test_cty_dat_lookup_us_call() -> None:
    cty = CtyDat.from_string(CTY_DAT_FIXTURE)
    r = cty.lookup("W1AW")
    assert r is not None
    assert r.entity.continent == "NA"


def test_cty_dat_lookup_unknown() -> None:
    cty = CtyDat.from_string(CTY_DAT_FIXTURE)
    assert cty.lookup("ZZZZZ") is None


# ============================================================================
# QRZ
# ============================================================================
@respx.mock
async def test_qrz_session_login_and_callsign_lookup() -> None:
    respx.get("https://xmldata.qrz.com/xml/current/").mock(
        side_effect=[
            Response(
                200,
                text=(
                    '<?xml version="1.0"?>'
                    '<QRZDatabase xmlns="http://xmldata.qrz.com">'
                    "<Session><Key>ABCD1234</Key></Session>"
                    "</QRZDatabase>"
                ),
            ),
            Response(
                200,
                text=(
                    '<?xml version="1.0"?>'
                    '<QRZDatabase xmlns="http://xmldata.qrz.com">'
                    "<Callsign>"
                    "<call>W1AW</call>"
                    "<fname>HIRAM</fname><name>MAXIM</name>"
                    "<grid>FN31</grid><country>United States</country>"
                    "</Callsign>"
                    "</QRZDatabase>"
                ),
            ),
        ]
    )

    async with QrzClient(user="dk9xr", password="x"):
        client = QrzClient(user="dk9xr", password="x")
        rec = await client.callsign("W1AW")
        assert rec is not None
        assert rec.first_name == "HIRAM"
        assert rec.grid == "FN31"


# ============================================================================
# HamQTH
# ============================================================================
@respx.mock
async def test_hamqth_callsign_lookup() -> None:
    respx.get("https://www.hamqth.com/xml.php").mock(
        side_effect=[
            Response(
                200,
                text=(
                    '<?xml version="1.0"?>'
                    '<HamQTH xmlns="https://www.hamqth.com">'
                    "<session><session_id>SID-XYZ</session_id></session>"
                    "</HamQTH>"
                ),
            ),
            Response(
                200,
                text=(
                    '<?xml version="1.0"?>'
                    '<HamQTH xmlns="https://www.hamqth.com">'
                    "<search>"
                    "<callsign>DK9XR</callsign>"
                    "<nick>Seb</nick>"
                    "<grid>JN58td</grid>"
                    "<country>Germany</country>"
                    "</search>"
                    "</HamQTH>"
                ),
            ),
        ]
    )

    client = HamQthClient(user="x", password="y")
    rec = await client.callsign("DK9XR")
    assert rec is not None
    assert rec.name == "Seb"
    assert rec.grid == "JN58td"


# ============================================================================
# HamQSL solar
# ============================================================================
@respx.mock
async def test_hamqsl_solar_parses_n0nbh_format() -> None:
    respx.get("https://www.hamqsl.com/solarxml.php").mock(
        return_value=Response(
            200,
            text=(
                "<solar><solardata>"
                "<solarflux>150</solarflux>"
                "<aindex>12</aindex>"
                "<kindex>3</kindex>"
                "<sunspots>140</sunspots>"
                "<xray>C1.2</xray>"
                "<aurora>4</aurora>"
                "<updated>2026-05-15 12:00 GMT</updated>"
                "</solardata></solar>"
            ),
        )
    )
    client = HamQslClient()
    sd = await client.solar()
    assert sd is not None
    assert sd.sfi == 150
    assert sd.k_index == 3
    assert sd.x_ray == "C1.2"


@respx.mock
async def test_hamqsl_returns_stale_on_error() -> None:
    # First call succeeds, populates cache; second call fails — should
    # return stale instead of None.
    respx.get("https://www.hamqsl.com/solarxml.php").mock(
        side_effect=[
            Response(
                200,
                text=(
                    "<solar><solardata>"
                    "<solarflux>100</solarflux></solardata></solar>"
                ),
            ),
            Response(500),
        ]
    )
    client = HamQslClient(cache_ttl_s=0.01)
    first = await client.solar()
    assert first is not None and first.sfi == 100
    await asyncio.sleep(0.05)  # let cache go stale
    second = await client.solar()
    assert second is not None and second.sfi == 100  # fell back to stale


# ============================================================================
# PSK Reporter download
# ============================================================================
@respx.mock
async def test_psk_reporter_who_heard_me() -> None:
    respx.get("https://pskreporter.info/cgi-bin/pskquery5.pl").mock(
        return_value=Response(
            200,
            text=(
                "<receptionReports>"
                '<receptionReport receiverCallsign="JA1ABC" receiverLocator="PM95"'
                ' sNR="-10" frequency="14076000" mode="FT8" flowStartSeconds="1700000000"/>'
                '<receptionReport receiverCallsign="VK6XYZ" receiverLocator="OF87"'
                ' sNR="-22" frequency="14076000" mode="FT8" flowStartSeconds="1700001000"/>'
                "</receptionReports>"
            ),
        )
    )
    client = PskReporterClient()
    rs = await client.who_heard_me("DK9XR", hours=6)
    assert len(rs) == 2
    assert rs[0].rx_call == "JA1ABC"
    assert rs[0].band == "20m"


async def test_psk_reporter_upload_is_noop_when_disabled() -> None:
    client = PskReporterClient(enabled=False)
    await client.upload_decode(
        sender_call="DK9XR",
        sender_grid="JN58td",
        rx_callsign="W1AW",
        snr_db=-10,
        band_hz=14_076_000,
    )
    # no exception = pass


# ============================================================================
# ntfy
# ============================================================================
@respx.mock
async def test_ntfy_post_includes_headers() -> None:
    route = respx.post("https://ntfy.sh/my-topic").mock(return_value=Response(200))
    client = NtfyClient(topic="my-topic")
    ok = await client.notify("hello", title="FT8", priority="high", tags=["radio"])
    assert ok is True
    assert route.called
    req = route.calls.last.request
    assert req.headers.get("Title") == "FT8"
    assert req.headers.get("Priority") == "high"
    assert req.headers.get("Tags") == "radio"


async def test_ntfy_disabled_when_no_topic() -> None:
    client = NtfyClient(topic=None)
    assert await client.notify("ignored") is False


# ============================================================================
# Blitzortung
# ============================================================================
def test_haversine_known_distance() -> None:
    # Nuremberg to Munich is ~150 km
    nbg = (49.45, 11.08)
    muc = (48.14, 11.58)
    d = haversine_km(nbg, muc)
    assert 140 < d < 160


def test_blitzortung_alarm_within_radius() -> None:
    bz = BlitzortungClient(alarm_radius_km=30)
    here = (49.45, 11.08)
    far = Strike(ts=datetime.now(UTC), lat=48.14, lon=11.58)  # 150km
    near = Strike(ts=datetime.now(UTC), lat=49.50, lon=11.10)  # ~6km
    bz.ingest(far)
    assert bz.is_storm_nearby(here) is False
    bz.ingest(near)
    assert bz.is_storm_nearby(here) is True


def test_blitzortung_prunes_old_strikes() -> None:
    bz = BlitzortungClient(alarm_radius_km=30, retention_minutes=10)
    old = Strike(
        ts=datetime.now(UTC) - timedelta(minutes=20),
        lat=49.50,
        lon=11.10,
    )
    bz.ingest(old)
    assert bz.nearest_strike_km((49.45, 11.08)) is None
