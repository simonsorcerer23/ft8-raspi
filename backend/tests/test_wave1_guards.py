"""Welle 1 des Fix-Plans zum Tiefenaudit 2026-09-06.

Drei Sicherheitszusagen aus architecture.md waren im Code nie verdrahtet
oder falsch verdrahtet:

* A1  Der Zeit-Guard vertraute dem GPS-Fix — aber GPS stellt auf diesem
      System die Uhr nicht (chrony laeuft NTP-only). Jetzt zaehlt nur chrony.
* A2  Die IARU-Segment-Sperre existierte nur auf dem Papier; die grobe
      Banderkennung hatte Region-2-Kanten. Jetzt: Dial-Guard gegen die
      konfigurierten FT8/FT4-Dials.
* A3  ``set-freq`` nahm jede Frequenz — ueber den engen ntfy-Token, der in
      oeffentlichen Topics steht. Jetzt: nur konfigurierte Dials.
* B2  Akku-Guard mit festen 12,0 V bei einem 7,4-V-Akku — jetzt
      konfigurierbar, Default aus.
* B3  PTT wurde beim Start nie geraeumt.
* C2  Lesefehler wurden als Idealwerte eingesetzt (Offset 0,0 / 50 Grad).
* C3  Guard-Limits wurden nicht hot-reloaded.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from ft8_appliance.config import (
    AntennaConfig,
    AppConfig,
    BandConfig,
    OperatingConfig,
    OperatorConfig,
)
from ft8_appliance.rig.rigctld_client import RigSnapshot
from ft8_appliance.runtime import FakeSlotClock, Orchestrator
from ft8_appliance.runtime import orchestrator as orchestrator_mod
from ft8_appliance.statemachine.guards import (
    DEFAULT_GUARDS,
    GuardLimits,
    HardwareState,
    battery_guard,
    dial_guard,
    evaluate,
    first_failure,
    temp_guard,
    time_guard,
)
from ft8_appliance.util.system_health import ChronyStatus, parse_chrony_tracking

# ------------------------------------------------------------------ Helfer


def _cfg(**operating) -> AppConfig:
    return AppConfig(
        operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
        bands=[
            BandConfig(name="20m", freq_khz=14074, antenna="endfed"),
            BandConfig(name="60m", freq_khz=5357, antenna="endfed"),
        ],
        antennas=[AntennaConfig(name="endfed", bands=["20m", "60m"])],
        operating=OperatingConfig(**operating),
    )


def _orch(snapshots: list[RigSnapshot] | None = None, cfg: AppConfig | None = None) -> Orchestrator:
    rig = AsyncMock()
    rig.snapshot = AsyncMock(side_effect=list(snapshots or [RigSnapshot(freq_hz=14_074_000)] * 5))
    rig.close = AsyncMock(return_value=None)
    rig.set_ptt = AsyncMock(return_value=None)
    gps = AsyncMock()
    gps.snapshot = type("S", (), {"mode": 3, "lat": 0, "lon": 0, "ts": None,
                                  "lock_for_min": None, "satellites_used": None})()
    gps.close = AsyncMock(return_value=None)

    async def _no_decodes(tick):
        return []

    return Orchestrator(config=cfg or _cfg(), rig=rig, gps=gps,
                        decode_source=_no_decodes, slot_clock=FakeSlotClock(count=0))


class _StopLoop(Exception):
    pass


async def _poll_n(orch: Orchestrator, n: int, monkeypatch) -> None:
    """Den ECHTEN _rig_poll_loop n Runden laufen lassen."""
    real_sleep = asyncio.sleep
    calls = {"n": 0}

    async def fake_sleep(delay: float, *a, **kw):
        if delay >= 1.0:
            calls["n"] += 1
            if calls["n"] >= n:
                raise _StopLoop
        return await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(_StopLoop):
        await orch._rig_poll_loop()


def _force_chrony(monkeypatch, status: ChronyStatus | None) -> None:
    async def _stub() -> ChronyStatus | None:
        return status
    monkeypatch.setattr(orchestrator_mod, "read_chrony_tracking", _stub)


def _tick():
    from datetime import UTC, datetime

    from ft8_appliance.runtime.slot_clock import SlotTick
    now = datetime.now(UTC)
    return SlotTick(index=0, posix=now.timestamp(), utc_start=now)


# ================================================================== A1 Zeit


def test_gps_fix_alone_does_not_unlock_tx() -> None:
    """Der Kern von A1: GPS-Fix ohne chrony-Sync ist KEINE Zeitquelle."""
    res = time_guard(HardwareState(gps_fix_mode=3, chrony_synced=False), GuardLimits())
    assert res.ok is False
    assert res.code == "guard.time_no_sync_gps_idle"  # Grund nennt das GPS


def test_no_time_source_at_all_is_reported_as_such() -> None:
    res = time_guard(HardwareState(gps_fix_mode=0, chrony_synced=False), GuardLimits())
    assert res.ok is False
    assert res.code == "guard.time_no_sync"


def test_chrony_synced_without_gps_is_enough() -> None:
    """Gegenstueck — die Absicht der Chaos-Tests aus eb1c662 bleibt."""
    res = time_guard(
        HardwareState(gps_fix_mode=0, chrony_synced=True, time_offset_s=0.02), GuardLimits(),
    )
    assert res.ok is True


def test_unknown_offset_blocks_even_when_chrony_claims_sync() -> None:
    res = time_guard(HardwareState(chrony_synced=True, time_offset_s=None), GuardLimits())
    assert res.ok is False
    assert res.code == "guard.time_unknown"


def test_offset_over_limit_still_blocks() -> None:
    res = time_guard(HardwareState(chrony_synced=True, time_offset_s=1.8), GuardLimits())
    assert res.ok is False
    assert res.code == "guard.time_offset"


_CHRONYC_GPS = """Reference ID    : 47505300 (GPS)
Stratum         : 1
Ref time (UTC)  : Sat Sep 06 10:00:00 2026
System time     : 0.000012345 seconds fast of NTP time
Last offset     : +0.000001000 seconds
RMS offset      : 0.000020000 seconds
Frequency       : 1.234 ppm slow
Residual freq   : +0.001 ppm
Skew            : 0.010 ppm
Root delay      : 0.000100000 seconds
Root dispersion : 0.000200000 seconds
Update interval : 16.0 seconds
Leap status     : Normal
"""

_CHRONYC_NTP = _CHRONYC_GPS.replace("47505300 (GPS)", "C0A80001 (ntp1.example.org)").replace(
    "Stratum         : 1", "Stratum         : 3",
)


def test_chrony_parser_reads_the_reference_name() -> None:
    gps = parse_chrony_tracking(_CHRONYC_GPS)
    ntp = parse_chrony_tracking(_CHRONYC_NTP)
    assert gps is not None and gps.ref_id_name == "GPS" and gps.stratum == 1
    assert ntp is not None and ntp.ref_id_name == "ntp1.example.org" and ntp.stratum == 3
    assert gps.offset_s == pytest.approx(0.000012345)


@pytest.mark.asyncio
async def test_unreadable_chrony_is_unknown_not_perfect(monkeypatch) -> None:
    """C2/A1 im Orchestrator: chronyc antwortet nicht -> Offset None, nicht 0,0,
    und der time_guard ist der gemeldete Grund."""
    _force_chrony(monkeypatch, None)
    orch = _orch()
    await orch._refresh_hardware_state(_tick())
    hw = orch._hardware_state
    assert hw.time_offset_s is None
    assert hw.chrony_synced is False
    failure = first_failure(evaluate(hw, GuardLimits()))
    assert failure is not None and failure.name == "time_guard"
    assert failure.code == "guard.time_no_sync_gps_idle"  # Sim-GPS hat Fix


@pytest.mark.asyncio
async def test_synced_chrony_reaches_the_guard(monkeypatch) -> None:
    _force_chrony(monkeypatch, ChronyStatus(offset_s=0.001, stratum=3, ref_id_name="ntp"))
    orch = _orch()
    await orch._refresh_hardware_state(_tick())
    assert time_guard(orch._hardware_state, GuardLimits()).ok is True


# ================================================================== A2 Dial


def test_dial_guard_passes_on_a_configured_dial() -> None:
    assert dial_guard(HardwareState(dial_on_configured_freq=True), GuardLimits()).ok is True


def test_dial_guard_blocks_off_dial_and_names_the_frequency() -> None:
    res = dial_guard(
        HardwareState(dial_on_configured_freq=False, rig_freq_hz=3_900_000),
        GuardLimits(dial_tolerance_hz=500),
    )
    assert res.ok is False
    assert res.code == "guard.dial"
    assert res.params == {"mhz": "3.9000", "tol": 500}


def test_dial_guard_has_no_opinion_without_a_frequency() -> None:
    """Frequenz unbekannt ist Sache des rig_link_guard."""
    assert dial_guard(HardwareState(dial_on_configured_freq=None), GuardLimits()).ok is True


def test_dial_guard_runs_before_the_band_derived_guards() -> None:
    names = [g.__name__ for g in DEFAULT_GUARDS]
    assert names.index("rig_link_guard") < names.index("dial_guard") < names.index("license_guard")


def test_off_dial_is_the_reported_reason_not_antenna() -> None:
    hw = HardwareState(dial_on_configured_freq=False, rig_freq_hz=3_900_000,
                       antenna_covers_band=False)
    failure = first_failure(evaluate(hw, GuardLimits()))
    assert failure is not None and failure.name == "dial_guard"


@pytest.mark.parametrize("hz,expected", [
    (14_074_000, True),    # FT8-Dial 20m
    (14_074_400, True),    # innerhalb 500 Hz
    (14_075_000, False),   # 1 kHz daneben = FT8-Segment verlassen
    (14_080_000, True),    # FT4-Default-Dial 20m zaehlt auch im FT8-Modus
    (5_357_000, True),     # 60m FT8
    (5_335_000, False),    # "60m" laut Region-2-Kante, aber kein Dial -> Off-Band in DL
    (3_900_000, False),    # "80m" laut Kante, nicht konfiguriert
])
def test_orchestrator_dial_check_uses_configured_dials(hz, expected) -> None:
    orch = _orch()
    orch._last_rig = RigSnapshot(freq_hz=hz)
    assert orch._dial_on_configured_freq() is expected


def test_orchestrator_dial_check_is_none_without_frequency() -> None:
    orch = _orch()
    orch._last_rig = RigSnapshot()
    assert orch._dial_on_configured_freq() is None


@pytest.mark.asyncio
async def test_hardware_state_carries_the_dial_verdict(monkeypatch) -> None:
    _force_chrony(monkeypatch, ChronyStatus(offset_s=0.0, stratum=3))
    orch = _orch()
    orch._last_rig = RigSnapshot(freq_hz=3_900_000)
    await orch._refresh_hardware_state(_tick())
    assert orch._hardware_state.dial_on_configured_freq is False
    assert orch._hardware_state.rig_freq_hz == 3_900_000


# ================================================================== A3 set-freq


def _route_orch(handled: list[int]) -> SimpleNamespace:
    real = _orch()

    async def handle_set_freq(hz: int) -> None:
        handled.append(hz)

    return SimpleNamespace(
        configured_dials_hz=real.configured_dials_hz,
        handle_set_freq=handle_set_freq,
        status=lambda: SimpleNamespace(state="IDLE"),
    )


@pytest.mark.asyncio
async def test_set_freq_accepts_a_configured_dial() -> None:
    from ft8_appliance.web.routes.control import SetFreqRequest, set_freq

    handled: list[int] = []
    resp = await set_freq(SetFreqRequest(freq_hz=14_074_000), orch=_route_orch(handled))
    assert resp.ok is True and handled == [14_074_000]


@pytest.mark.asyncio
async def test_set_freq_accepts_the_ft4_dial() -> None:
    from ft8_appliance.web.routes.control import SetFreqRequest, set_freq

    handled: list[int] = []
    await set_freq(SetFreqRequest(freq_hz=14_080_000), orch=_route_orch(handled))
    assert handled == [14_080_000]


@pytest.mark.asyncio
async def test_set_freq_rejects_an_arbitrary_frequency() -> None:
    """Der geleakte Action-Token kann den Sender nicht mehr beliebig parken."""
    from ft8_appliance.web.routes.control import SetFreqRequest, set_freq

    handled: list[int] = []
    with pytest.raises(HTTPException) as exc:
        await set_freq(SetFreqRequest(freq_hz=3_900_000), orch=_route_orch(handled))
    assert exc.value.status_code == 400
    assert handled == []


# ================================================================== B2 Akku


def test_battery_guard_is_off_at_zero() -> None:
    assert battery_guard(HardwareState(battery_v=7.4), GuardLimits(battery_min_v=0.0)).ok is True


def test_battery_guard_fires_when_configured() -> None:
    res = battery_guard(HardwareState(battery_v=7.4), GuardLimits(battery_min_v=11.0))
    assert res.ok is False and res.code == "guard.battery"


def test_battery_guard_ignores_missing_sensor() -> None:
    assert battery_guard(HardwareState(battery_v=None), GuardLimits(battery_min_v=11.0)).ok is True


def test_battery_default_is_off_in_config_and_limits() -> None:
    assert OperatingConfig().battery_min_v == 0.0
    assert GuardLimits().battery_min_v == 0.0


# ================================================================== C2 Temp


def test_temp_guard_passes_on_unreadable_sensor() -> None:
    assert temp_guard(HardwareState(cpu_temp_c=None), GuardLimits()).ok is True


def test_temp_guard_still_fires_on_a_real_reading() -> None:
    assert temp_guard(HardwareState(cpu_temp_c=90.0), GuardLimits()).ok is False


# ================================================================== B3 PTT


@pytest.mark.asyncio
async def test_start_clears_ptt_after_rig_connect() -> None:
    orch = _orch()
    orch.rig.connect = AsyncMock(return_value=None)
    await orch.start()
    try:
        orch.rig.set_ptt.assert_any_call(False)
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_poll_loop_clears_inherited_ptt_on_first_snapshot(monkeypatch) -> None:
    """rigctld haelt PTT ueber unseren Tod hinaus. Der neue Prozess sieht
    beim ersten brauchbaren Snapshot PTT=an, hat aber nie gesendet."""
    orch = _orch([RigSnapshot(freq_hz=14_074_000, ptt=True)] * 3)
    await _poll_n(orch, 1, monkeypatch)
    orch.rig.set_ptt.assert_called_with(False)
    assert orch.state_machine.ctx.last_lock_code == "lock.ptt_stuck"


@pytest.mark.asyncio
async def test_poll_loop_does_not_touch_our_own_ptt(monkeypatch) -> None:
    orch = _orch([RigSnapshot(freq_hz=14_074_000, ptt=True)] * 3)
    orch._last_tx_message_at = 123.0  # wir haben in diesem Prozess gesendet
    await _poll_n(orch, 1, monkeypatch)
    orch.rig.set_ptt.assert_not_called()


@pytest.mark.asyncio
async def test_poll_loop_waits_for_a_usable_snapshot(monkeypatch) -> None:
    """Leer-Snapshot (rigctld noch nicht bereit) entscheidet nichts —
    erst der erste mit Frequenz."""
    orch = _orch([RigSnapshot(ptt=True), RigSnapshot(freq_hz=14_074_000, ptt=True),
                  RigSnapshot(freq_hz=14_074_000, ptt=False)])
    await _poll_n(orch, 1, monkeypatch)
    orch.rig.set_ptt.assert_not_called()
    await _poll_n(orch, 1, monkeypatch)
    orch.rig.set_ptt.assert_called_with(False)


# ================================================================== C3 Hot-Reload


@pytest.mark.asyncio
async def test_guard_limits_follow_a_config_hot_reload() -> None:
    orch = _orch()
    assert orch.state_machine.limits.rig_link_max_age_s == 60.0
    new_cfg = _cfg(rig_link_max_age_s=120.0, battery_min_v=10.5, dial_tolerance_hz=800)
    await orch.on_config_changed(new_cfg)
    lim = orch.state_machine.limits
    assert lim.rig_link_max_age_s == 120.0
    assert lim.battery_min_v == 10.5
    assert lim.dial_tolerance_hz == 800


def test_every_config_backed_limit_is_hot_reloaded() -> None:
    """Quelltext-Gate: jedes GuardLimits-Feld, das ein OperatingConfig-
    Gegenstueck hat, muss in on_config_changed vorkommen — sonst liegt
    die naechste Einstellung wieder bis zum Neustart herum."""
    import dataclasses
    import inspect

    src = inspect.getsource(Orchestrator.on_config_changed)
    cfg_fields = set(OperatingConfig.model_fields)
    for f in dataclasses.fields(GuardLimits):
        if f.name in cfg_fields:
            assert f"limits.{f.name}" in src, f"{f.name} nicht im Hot-Reload"
