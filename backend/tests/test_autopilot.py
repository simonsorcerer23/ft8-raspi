"""Band/Mode-Autopilot tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

from ft8_appliance.config import (
    AntennaConfig,
    AppConfig,
    BandConfig,
    OperatingConfig,
    OperatorConfig,
)
from ft8_appliance.rig.rigctld_client import RigSnapshot
from ft8_appliance.runtime import FakeSlotClock, Orchestrator
from ft8_appliance.runtime.orchestrator import AutopilotStats


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
    ft8_attempts: int = 0,
    ft8_completed: int = 0,
    ft4_attempts: int = 0,
    ft4_completed: int = 0,
) -> dict[tuple[str, str], AutopilotStats]:
    return {
        (band, "FT8"): AutopilotStats(
            band=band,
            mode="FT8",
            decodes=decodes,
            attempts=ft8_attempts,
            completed=ft8_completed,
        ),
        (band, "FT4"): AutopilotStats(
            band=band,
            mode="FT4",
            decodes=decodes,
            attempts=ft4_attempts,
            completed=ft4_completed,
        ),
    }


def test_autopilot_defaults_are_policy_limited_to_15m() -> None:
    op = OperatingConfig()
    assert op.autopilot_enabled is False
    assert op.autopilot_allowed_bands == ["15m"]
    assert op.autopilot_allowed_modes == ["FT8", "FT4"]


def test_autopilot_prefers_ft4_on_active_15m() -> None:
    op = OperatingConfig(
        autopilot_enabled=True,
        autopilot_allowed_bands=["15m"],
        autopilot_allowed_modes=["FT8", "FT4"],
    )
    orch = _orch(_cfg(operating=op))
    decision = orch._autopilot_decision("15m", "FT8", _stats("15m", decodes=80))
    assert decision is not None
    assert decision.band == "15m"
    assert decision.mode == "FT4"


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
