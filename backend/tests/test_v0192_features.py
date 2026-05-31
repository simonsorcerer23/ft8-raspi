"""Tests v0.19.2 — Watchlist source-aware push + ng3k rarity gating."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock, AsyncMock

import pytest

from ft8_appliance.config.models import OperatingConfig
from ft8_appliance.statemachine.states import DecodedMsg


def _decode(call_from="ZL9HR", snr=-5):
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call_from, call_to=None, grid=None,
        message=f"CQ {call_from}", snr_db=snr, dt_s=0.1,
        freq_offset_hz=1500, band="15m",
    )


# ---------------------------------------------------------------------------
# OperatingConfig
# ---------------------------------------------------------------------------


def test_config_defaults():
    cfg = OperatingConfig()
    assert cfg.dxped_ng3k_push_enabled is True
    assert cfg.dxped_ng3k_push_min_rarity == 50


def test_config_rarity_range():
    cfg = OperatingConfig(dxped_ng3k_push_min_rarity=0)
    assert cfg.dxped_ng3k_push_min_rarity == 0
    cfg = OperatingConfig(dxped_ng3k_push_min_rarity=100)
    assert cfg.dxped_ng3k_push_min_rarity == 100


def test_config_rarity_clamped():
    """ge=0, le=100 — Pydantic raises ValidationError outside range."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        OperatingConfig(dxped_ng3k_push_min_rarity=-1)
    with pytest.raises(ValidationError):
        OperatingConfig(dxped_ng3k_push_min_rarity=101)


# ---------------------------------------------------------------------------
# _fire_watchlist_alert — source-aware logic
#
# Wir testen die Logik in Isolation via Mock-Orchestrator (kein full
# orchestrator-Boot noetig).
# ---------------------------------------------------------------------------


async def _build_mock_orch(
    *, push_enabled=True, min_rarity=50,
    watchlist_sources=None,
    last_alert=None,
):
    """Mock-Orchestrator mit Minimal-Setup fuer _fire_watchlist_alert."""
    from ft8_appliance.runtime.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch._watchlist_sources = watchlist_sources or {}
    orch._watchlist_last_alert = last_alert or {}
    # config-Mock
    op_cfg = MagicMock()
    op_cfg.dxped_ng3k_push_enabled = push_enabled
    op_cfg.dxped_ng3k_push_min_rarity = min_rarity
    config = MagicMock()
    config.operating = op_cfg
    orch.config = config
    # ntfy-Mock
    integrations = MagicMock()
    ntfy_mock = MagicMock()
    ntfy_mock.enabled = True
    ntfy_mock.notify = AsyncMock()
    integrations.ntfy = ntfy_mock
    orch.integrations = integrations
    # state_machine-Mock
    sm = MagicMock()
    sm.ctx.band = "15m"
    orch.state_machine = sm
    return orch, ntfy_mock


@pytest.mark.asyncio
async def test_manual_source_pushes_without_rarity_gate():
    """User-Eintrag pushen IMMER (kein Rarity-Filter)."""
    orch, ntfy = await _build_mock_orch(
        push_enabled=False,  # NG3K-Gate aus, sollte manual NICHT beeinflussen
        min_rarity=99,
        watchlist_sources={"DL5ABC": "manual"},
    )
    await orch._fire_watchlist_alert("DL5ABC", _decode("DL5ABC"))
    ntfy.notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_ng3k_source_blocked_when_globally_disabled():
    """Wenn dxped_ng3k_push_enabled=False → kein Push fuer ng3k_auto."""
    orch, ntfy = await _build_mock_orch(
        push_enabled=False,
        watchlist_sources={"ZL9HR": "ng3k_auto"},
    )
    await orch._fire_watchlist_alert("ZL9HR", _decode("ZL9HR"))
    ntfy.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_ng3k_source_blocked_below_rarity_threshold():
    """Galapagos (~30) unter Threshold 50 → kein Push."""
    orch, ntfy = await _build_mock_orch(
        push_enabled=True,
        min_rarity=50,
        watchlist_sources={"HD8R": "ng3k_auto"},  # Galapagos, rarity ~30
    )
    await orch._fire_watchlist_alert("HD8R", _decode("HD8R"))
    ntfy.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_ng3k_source_pushes_above_rarity_threshold():
    """Nordkorea P5 (~100) ueber Threshold 50 → push."""
    orch, ntfy = await _build_mock_orch(
        push_enabled=True,
        min_rarity=50,
        watchlist_sources={"P5RYL": "ng3k_auto"},  # P5, rarity ~100
    )
    await orch._fire_watchlist_alert("P5RYL", _decode("P5RYL"))
    ntfy.notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_throttle_manual_1h():
    """Manual: 2. Push innerhalb 1h wird unterdrueckt."""
    import time as _time
    now = _time.time()
    orch, ntfy = await _build_mock_orch(
        watchlist_sources={"DL5ABC": "manual"},
        last_alert={"DL5ABC": now - 600},  # vor 10 min
    )
    await orch._fire_watchlist_alert("DL5ABC", _decode("DL5ABC"))
    ntfy.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_throttle_ng3k_24h():
    """ng3k_auto: 2. Push innerhalb 24h wird unterdrueckt."""
    import time as _time
    now = _time.time()
    orch, ntfy = await _build_mock_orch(
        push_enabled=True, min_rarity=0,
        watchlist_sources={"P5RYL": "ng3k_auto"},
        last_alert={"P5RYL": now - 3700},  # vor ~1h
    )
    await orch._fire_watchlist_alert("P5RYL", _decode("P5RYL"))
    ntfy.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_throttle_manual_after_1h_pushes_again():
    import time as _time
    now = _time.time()
    orch, ntfy = await _build_mock_orch(
        watchlist_sources={"DL5ABC": "manual"},
        last_alert={"DL5ABC": now - 3700},  # vor 1h+
    )
    await orch._fire_watchlist_alert("DL5ABC", _decode("DL5ABC"))
    ntfy.notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_source_defaults_to_manual():
    """Wenn source nicht im Dict (Legacy-Eintrag), fall back auf 'manual'."""
    orch, ntfy = await _build_mock_orch(
        watchlist_sources={},  # keine source-Info
    )
    await orch._fire_watchlist_alert("DL5ABC", _decode("DL5ABC"))
    ntfy.notify.assert_awaited_once()
