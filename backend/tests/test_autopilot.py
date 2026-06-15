"""Band/Mode-Autopilot tests."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from ft8_appliance.config import (
    AntennaConfig,
    AppConfig,
    BandConfig,
    OperatingConfig,
    OperatorConfig,
)
from ft8_appliance.db import create_all, init_engine, repository, session_scope
from ft8_appliance.rig.rigctld_client import RigSnapshot
from ft8_appliance.runtime import FakeSlotClock, Orchestrator
from ft8_appliance.runtime.orchestrator import AutopilotDecision, AutopilotStats


def _cfg(*, operating: OperatingConfig | None = None) -> AppConfig:
    return AppConfig(
        operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
        bands=[
            BandConfig(name="15m", freq_khz=21074, antenna="endfed"),
            BandConfig(name="20m", freq_khz=14074, antenna="endfed"),
        ],
        antennas=[AntennaConfig(name="endfed", bands=["15m", "20m"])],
        operating=operating or OperatingConfig(),
    )


def _orch(cfg: AppConfig) -> Orchestrator:
    rig = AsyncMock()
    gps = AsyncMock()
    gps.snapshot = type(
        "Gps",
        (),
        {
            "mode": 3,
            "lat": None,
            "lon": None,
            "ts": None,
            "lock_for_min": None,
            "satellites_used": None,
        },
    )()

    async def _no_decodes(_tick):
        return []

    return Orchestrator(
        config=cfg,
        rig=rig,
        gps=gps,
        decode_source=_no_decodes,
        slot_clock=FakeSlotClock(count=0),
        db_enabled=False,
    )


def _stats(
    band: str,
    *,
    decodes: int,
    ft8_decodes: int | None = None,
    ft4_decodes: int | None = None,
    ft8_attempts: int = 0,
    ft8_completed: int = 0,
    ft4_attempts: int = 0,
    ft4_completed: int = 0,
) -> dict[tuple[str, str], AutopilotStats]:
    return {
        (band, "FT8"): AutopilotStats(
            band=band,
            mode="FT8",
            decodes=decodes if ft8_decodes is None else ft8_decodes,
            attempts=ft8_attempts,
            completed=ft8_completed,
        ),
        (band, "FT4"): AutopilotStats(
            band=band,
            mode="FT4",
            decodes=decodes if ft4_decodes is None else ft4_decodes,
            attempts=ft4_attempts,
            completed=ft4_completed,
        ),
    }


def test_autopilot_defaults_are_policy_limited_to_15m() -> None:
    op = OperatingConfig()
    assert op.autopilot_enabled is False
    assert op.autopilot_allowed_bands == ["15m"]
    assert op.autopilot_allowed_modes == ["FT8", "FT4"]
    assert op.autopilot_ft4_probe_min_decodes == 150
    assert op.autopilot_ft4_probe_dwell_min == 5
    assert op.autopilot_ft4_null_probe_threshold == 2
    assert op.autopilot_ft4_null_cooldown_min == 120
    assert op.autopilot_ft4_null_cooldown_max_min == 360


def test_autopilot_prefers_ft4_on_active_15m() -> None:
    op = OperatingConfig(
        autopilot_enabled=True,
        autopilot_allowed_bands=["15m"],
        autopilot_allowed_modes=["FT8", "FT4"],
    )
    orch = _orch(_cfg(operating=op))
    decision = orch._autopilot_decision("15m", "FT8", _stats("15m", decodes=180))
    assert decision is not None
    assert decision.band == "15m"
    assert decision.mode == "FT4"


def test_autopilot_stays_on_ft8_below_strict_ft4_probe_threshold() -> None:
    op = OperatingConfig(
        autopilot_enabled=True,
        autopilot_allowed_bands=["15m"],
        autopilot_allowed_modes=["FT8", "FT4"],
    )
    orch = _orch(_cfg(operating=op))
    decision = orch._autopilot_decision("15m", "FT8", _stats("15m", decodes=80))
    assert decision is None


def test_autopilot_falls_back_to_ft8_when_ft4_completion_is_bad() -> None:
    op = OperatingConfig(
        autopilot_enabled=True,
        autopilot_allowed_bands=["15m"],
        autopilot_allowed_modes=["FT8", "FT4"],
    )
    orch = _orch(_cfg(operating=op))
    decision = orch._autopilot_decision(
        "15m",
        "FT4",
        _stats("15m", decodes=90, ft4_attempts=10, ft4_completed=0),
    )
    assert decision is not None
    assert decision.band == "15m"
    assert decision.mode == "FT8"


def test_autopilot_pauses_ft4_after_repeated_null_probes() -> None:
    op = OperatingConfig(
        autopilot_enabled=True,
        autopilot_allowed_bands=["15m"],
        autopilot_allowed_modes=["FT8", "FT4"],
        autopilot_ft4_null_probe_threshold=1,
        autopilot_ft4_null_cooldown_min=60,
    )
    orch = _orch(_cfg(operating=op))
    decision = AutopilotDecision(
        band="15m",
        mode="FT8",
        reason="decode density low",
        score=0.0,
    )
    orch._autopilot_record_ft4_probe_result(
        "15m",
        "FT4",
        decision,
        _stats("15m", decodes=0, ft8_decodes=300, ft4_decodes=0),
    )

    mode, reason = orch._autopilot_mode_for_band(
        "15m",
        ["FT8", "FT4"],
        _stats("15m", decodes=300),
    )
    assert mode == "FT8"
    assert "FT4 paused" in reason


def test_autopilot_ft4_null_probe_backoff_expands_until_cap() -> None:
    op = OperatingConfig(
        autopilot_enabled=True,
        autopilot_allowed_bands=["15m"],
        autopilot_allowed_modes=["FT8", "FT4"],
        autopilot_ft4_null_probe_threshold=1,
        autopilot_ft4_null_cooldown_min=5,
        autopilot_ft4_null_cooldown_max_min=20,
    )
    orch = _orch(_cfg(operating=op))
    decision = AutopilotDecision(
        band="15m",
        mode="FT8",
        reason="null probe",
        score=0.0,
    )

    orch._autopilot_record_ft4_probe_result(
        "15m",
        "FT4",
        decision,
        _stats("15m", decodes=0, ft8_decodes=300, ft4_decodes=0),
    )
    first = orch._autopilot_ft4_block_remaining_s("15m")
    orch._autopilot_ft4_blocked_until["15m"] = time.monotonic() - 1
    orch._autopilot_record_ft4_probe_result(
        "15m",
        "FT4",
        decision,
        _stats("15m", decodes=0, ft8_decodes=300, ft4_decodes=0),
    )
    second = orch._autopilot_ft4_block_remaining_s("15m")

    assert 4 * 60 < first <= 5 * 60
    assert 9 * 60 < second <= 10 * 60


def test_autopilot_ft4_probe_escape_after_short_null_dwell() -> None:
    op = OperatingConfig(
        autopilot_enabled=True,
        autopilot_allowed_bands=["15m"],
        autopilot_allowed_modes=["FT8", "FT4"],
        autopilot_ft4_probe_dwell_min=5,
    )
    orch = _orch(_cfg(operating=op))
    now = time.monotonic()
    orch._autopilot_last_switch_at = now - 5 * 60 - 1

    assert orch._autopilot_ft4_probe_due("15m", "FT4", ["FT8", "FT4"], now)
    decision = orch._autopilot_ft4_probe_escape(
        "15m",
        ["FT8", "FT4"],
        _stats("15m", decodes=0, ft8_decodes=220, ft4_decodes=0),
    )

    assert decision is not None
    assert decision.band == "15m"
    assert decision.mode == "FT8"
    assert "null result" in decision.reason


def test_frequency_tamper_suppressed_during_boot_reconciliation() -> None:
    orch = _orch(_cfg(operating=OperatingConfig(mode="FT4")))
    orch._freq_tamper_reconcile_deadline = time.monotonic() + 30

    ready = orch._frequency_tamper_ready(
        actual_hz=21_074_000,
        expected_hz=21_140_000,
        band_name="15m",
    )

    assert ready is False
    assert orch._freq_tamper_reconciled is False
    assert orch._last_boot_freq_drift_log_hz == -66_000


def test_frequency_tamper_arms_after_boot_dial_matches_mode() -> None:
    orch = _orch(_cfg(operating=OperatingConfig(mode="FT8")))

    ready = orch._frequency_tamper_ready(
        actual_hz=21_074_000,
        expected_hz=21_074_000,
        band_name="15m",
    )

    assert ready is True
    assert orch._freq_tamper_reconciled is True


def test_frequency_tamper_allows_persistent_drift_after_boot_grace() -> None:
    orch = _orch(_cfg(operating=OperatingConfig(mode="FT4")))
    orch._freq_tamper_reconcile_deadline = time.monotonic() - 1

    ready = orch._frequency_tamper_ready(
        actual_hz=21_074_000,
        expected_hz=21_140_000,
        band_name="15m",
    )

    assert ready is True
    assert orch._freq_tamper_reconciled is True


async def test_rig_ready_reconciles_boot_mode_dial_after_unknown_freq() -> None:
    orch = _orch(_cfg(operating=OperatingConfig(mode="FT4")))
    orch._last_rig = RigSnapshot(freq_hz=21_074_000)

    await orch._reconcile_dial_once_after_rig_ready()

    orch.rig.set_freq.assert_awaited_once_with(21_140_000)
    assert orch._boot_dial_reconcile_done is True


async def test_autopilot_collect_stats_counts_decodes_by_mode() -> None:
    init_engine(":memory:")
    await create_all()
    now = datetime.now(UTC)
    async with session_scope() as s:
        await repository.insert_decode(
            s,
            ts=now,
            call_from="W1AW",
            call_to=None,
            grid="FN31",
            message="CQ W1AW FN31",
            snr_db=-8,
            dt_s=0.2,
            freq_offset_hz=1500,
            band="15m",
            mode="FT8",
        )
        await repository.insert_decode(
            s,
            ts=now,
            call_from="K1ABC",
            call_to=None,
            grid="FN42",
            message="CQ K1ABC FN42",
            snr_db=-11,
            dt_s=0.1,
            freq_offset_hz=1600,
            band="15m",
            mode="FT8",
        )
        await repository.insert_decode(
            s,
            ts=now,
            call_from="DL3QR",
            call_to=None,
            grid="JO62",
            message="CQ DL3QR JO62",
            snr_db=-5,
            dt_s=0.0,
            freq_offset_hz=1400,
            band="15m",
            mode="FT4",
        )
    op = OperatingConfig(
        autopilot_enabled=True,
        autopilot_allowed_bands=["15m"],
        autopilot_allowed_modes=["FT8", "FT4"],
    )
    orch = _orch(_cfg(operating=op))
    orch.db_enabled = True
    stats = await orch._autopilot_collect_stats(["15m"], ["FT8", "FT4"])
    assert stats[("15m", "FT8")].decodes == 2
    assert stats[("15m", "FT4")].decodes == 1


def test_autopilot_respects_allowed_modes() -> None:
    op = OperatingConfig(
        autopilot_enabled=True,
        autopilot_allowed_bands=["15m"],
        autopilot_allowed_modes=["FT8"],
    )
    orch = _orch(_cfg(operating=op))
    decision = orch._autopilot_decision("15m", "FT4", _stats("15m", decodes=120))
    assert decision is not None
    assert decision.band == "15m"
    assert decision.mode == "FT8"


def test_autopilot_respects_allowed_bands() -> None:
    op = OperatingConfig(
        autopilot_enabled=True,
        autopilot_allowed_bands=["15m"],
        autopilot_allowed_modes=["FT8", "FT4"],
    )
    orch = _orch(_cfg(operating=op))
    stats = _stats("15m", decodes=50)
    decision = orch._autopilot_decision("20m", "FT8", stats)
    assert decision is not None
    assert decision.band == "15m"


def test_current_band_detects_ft4_subband() -> None:
    orch = _orch(_cfg())
    orch._last_rig = RigSnapshot(freq_hz=21_140_000)
    assert orch._current_band() == "15m"
