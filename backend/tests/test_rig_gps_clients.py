"""Integration tests: production clients ↔ in-process mocks.

The mocks speak the *real* wire protocols, so every passing test here
also validates that the clients will work against the production
``rigctld`` / ``gpsd`` daemons on the Pi.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from ft8_appliance.gps import GpsdClient
from ft8_appliance.rig import RigctldClient, RigctldError
from tests.mocks.mock_gpsd import MockGpsd
from tests.mocks.mock_rigctld import MockRigctld


# ============================================================================
# Rig client
# ============================================================================
@pytest.mark.asyncio
async def test_rig_get_freq_via_mock() -> None:
    async with MockRigctld() as rig:
        client = RigctldClient(host="127.0.0.1", port=rig.port)
        await client.connect()
        assert await client.get_freq() == 14_074_000
        await client.close()


@pytest.mark.asyncio
async def test_rig_set_then_get_freq() -> None:
    async with MockRigctld() as rig:
        client = RigctldClient(host="127.0.0.1", port=rig.port)
        await client.connect()
        await client.set_freq(7_074_000)
        assert await client.get_freq() == 7_074_000
        await client.close()


@pytest.mark.asyncio
async def test_rig_ptt_toggle() -> None:
    async with MockRigctld() as rig:
        client = RigctldClient(host="127.0.0.1", port=rig.port)
        await client.connect()
        assert await client.get_ptt() is False
        await client.set_ptt(True)
        assert await client.get_ptt() is True
        await client.set_ptt(False)
        assert await client.get_ptt() is False
        await client.close()


@pytest.mark.asyncio
async def test_rig_snapshot_collects_everything() -> None:
    async with MockRigctld() as rig:
        rig.set_swr(1.4)
        client = RigctldClient(host="127.0.0.1", port=rig.port)
        await client.connect()
        snap = await client.snapshot()
        assert snap.freq_hz == 14_074_000
        assert snap.mode == "USB"
        assert snap.ptt is False
        assert snap.swr == pytest.approx(1.4)
        assert snap.rfpower_norm == pytest.approx(1.0)
        await client.close()


@pytest.mark.asyncio
async def test_rig_set_mode_then_ptt() -> None:
    async with MockRigctld() as rig:
        client = RigctldClient(host="127.0.0.1", port=rig.port)
        await client.connect()
        await client.set_mode("USB", 2700)
        mode, bw = await client.get_mode()
        assert mode == "USB" and bw == 2700
        await client.set_ptt(True)
        assert await client.get_ptt() is True
        await client.close()


# ============================================================================
# GPS client
# ============================================================================
@pytest.mark.asyncio
async def test_gps_snapshot_after_connect() -> None:
    async with MockGpsd() as gpsd:
        client = GpsdClient(host="127.0.0.1", port=gpsd.port)
        task = asyncio.create_task(client.run_forever())
        # Mock sends a TPV+SKY in response to ?WATCH; give the loop time
        await asyncio.sleep(0.2)
        snap = client.snapshot
        assert snap.mode == 3
        assert snap.lat is not None and snap.lon is not None
        assert snap.sats_seen == 11 and snap.sats_used == 8
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await client.close()


@pytest.mark.asyncio
async def test_gps_snapshot_tracks_emit() -> None:
    """Mock can push fresh TPVs; client snapshot must update."""
    async with MockGpsd() as gpsd:
        gpsd.set_fix(lat=51.0, lon=10.0, mode=3)
        client = GpsdClient(host="127.0.0.1", port=gpsd.port)
        task = asyncio.create_task(client.run_forever())
        await asyncio.sleep(0.2)
        assert client.snapshot.lat == pytest.approx(51.0)

        # Move the fix and emit
        gpsd.set_fix(lat=52.5, lon=13.4)
        await gpsd.emit_tpv()
        await asyncio.sleep(0.1)
        assert client.snapshot.lat == pytest.approx(52.5)
        assert client.snapshot.lon == pytest.approx(13.4)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await client.close()
