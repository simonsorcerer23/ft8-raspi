"""Pre-flight guards executed before every TX transition.

A *Guard* is a callable that, given current observed state, decides
whether the next TX is allowed. Each returns a :class:`GuardResult`
which the state machine collects; the first non-OK result short-circuits
TX (state -> TX_LOCKED) and the reason is surfaced to the UI.

This module is intentionally pure and dependency-free, so unit-tests
can construct any combination of pass/fail scenarios cheaply.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GuardResult:
    ok: bool
    name: str
    # i18n message key + substitution params for the lock reason. The actual
    # localized string is produced at serialize time (browser request lang)
    # or with the config default lang for logs/ntfy — see ft8_appliance.i18n.
    # Kept dependency-free here so this module stays pure/unit-testable.
    code: str | None = None
    params: dict[str, object] | None = None


@dataclass(slots=True)
class HardwareState:
    """Snapshot of the live measurements the guards reason over."""

    gps_fix_mode: int = 3  # 0=no, 2=2D, 3=3D
    time_offset_s: float = 0.0  # |chrony / GPS offset|
    swr: float = 1.2
    alc_pct: int = 0
    battery_v: float | None = None  # None = on external power
    cpu_temp_c: float = 50.0
    audio_drift_samples: int = 0
    # Antenna lockout: True if the active antenna covers the current band
    # (orchestrator computes via AppConfig.can_tx_on()). Default True so
    # legacy tests don't trip; production wiring sets this every slot.
    antenna_covers_band: bool = True
    # Chrony has reached an upstream NTP source — used as a fallback when
    # GPS has no fix (indoor installs, basement shacks). chrony stratum
    # 2-3 with sub-100 ms offset is more than tight enough for FT8.
    chrony_synced: bool = False


@dataclass(slots=True)
class GuardLimits:
    """Thresholds — wired from :class:`AppConfig` at runtime."""

    swr_max: float = 2.0
    alc_max: int = 0
    battery_min_v: float = 12.0
    cpu_temp_max_c: float = 75.0
    audio_drift_warn_samples: int = 5
    audio_drift_fail_samples: int = 50
    time_offset_max_s: float = 0.5


Guard = Callable[[HardwareState, GuardLimits], GuardResult]


# ---------------------------------------------------------------------------
def time_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    # FT8 only needs ~100 ms time accuracy. Two sources can deliver that:
    # GPS fix (authoritative) or a chrony daemon synced to an NTP source.
    # Either is sufficient as long as the |offset| stays within the limit;
    # we only block when both are unavailable.
    # Reasons are emitted as i18n code + params (Sebastian 2026-06-01) and
    # localized at serialize time / with the config lang — see i18n.py.
    has_gps = hw.gps_fix_mode >= 2
    has_chrony = hw.chrony_synced
    if not has_gps and not has_chrony:
        return GuardResult(False, "time_guard", "guard.time_no_sync")
    if abs(hw.time_offset_s) > lim.time_offset_max_s:
        return GuardResult(
            False, "time_guard", "guard.time_offset",
            {"offset": f"{hw.time_offset_s:+.3f}", "max": lim.time_offset_max_s},
        )
    return GuardResult(True, "time_guard")


def swr_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    if hw.swr > lim.swr_max:
        return GuardResult(
            False, "swr_guard", "guard.swr",
            {"swr": f"{hw.swr:.2f}", "max": f"{lim.swr_max:.2f}"},
        )
    return GuardResult(True, "swr_guard")


def alc_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    if hw.alc_pct > lim.alc_max:
        return GuardResult(
            False, "alc_guard", "guard.alc",
            {"alc": hw.alc_pct, "max": lim.alc_max},
        )
    return GuardResult(True, "alc_guard")


def battery_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    if hw.battery_v is None:
        return GuardResult(True, "battery_guard")  # external power, no check
    if hw.battery_v < lim.battery_min_v:
        return GuardResult(
            False, "battery_guard", "guard.battery",
            {"volts": f"{hw.battery_v:.1f}", "min": lim.battery_min_v},
        )
    return GuardResult(True, "battery_guard")


def temp_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    if hw.cpu_temp_c > lim.cpu_temp_max_c:
        return GuardResult(
            False, "temp_guard", "guard.temp",
            {"temp": f"{hw.cpu_temp_c:.1f}", "max": lim.cpu_temp_max_c},
        )
    return GuardResult(True, "temp_guard")


def audio_drift_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    drift = abs(hw.audio_drift_samples)
    if drift > lim.audio_drift_fail_samples:
        return GuardResult(
            False, "audio_drift_guard", "guard.audio_drift", {"drift": drift},
        )
    return GuardResult(True, "audio_drift_guard")


def antenna_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    """Prevent TX on a band our active antenna can't handle.

    The orchestrator computes ``antenna_covers_band`` per slot from the
    current ``rig.freq_hz`` and the configured antenna profiles. If the
    user is parked on a band the antenna isn't rated for, TX is blocked.
    """
    if not hw.antenna_covers_band:
        return GuardResult(False, "antenna_guard", "guard.antenna")
    return GuardResult(True, "antenna_guard")


# Default ordered pipeline. Order matters: cheap pure-cpu checks first,
# then external-state checks. Stops at first failure.
DEFAULT_GUARDS: tuple[Guard, ...] = (
    time_guard,
    audio_drift_guard,
    antenna_guard,
    swr_guard,
    alc_guard,
    battery_guard,
    temp_guard,
)


def evaluate(
    hw: HardwareState,
    limits: GuardLimits,
    guards: tuple[Guard, ...] = DEFAULT_GUARDS,
) -> list[GuardResult]:
    """Run all *guards*; return their results in order."""
    return [g(hw, limits) for g in guards]


def first_failure(results: list[GuardResult]) -> GuardResult | None:
    """Return the first non-ok guard result, or ``None`` if all green."""
    return next((r for r in results if not r.ok), None)
