"""Band/Mode-Autopilot tests."""

from __future__ import annotations

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
    assert op.autopilot_ft4_null_probe_threshold == 2
    assert op.autopilot_ft4_null_cooldown_min == 120


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
