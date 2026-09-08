"""Audit 2026-07-30: bei totem Rig ging die ganze Guard-Kette auf gruen.

``RigctldClient.snapshot()`` wirft nie — jedes Feld haengt in einem
eigenen ``try/except``, damit ein fehlendes Hamlib-Level nicht den
gesamten Snapshot kippt. Ist rigctld tot oder das USB-Kabel raus, kommt
darum kein Fehler zurueck, sondern ein Snapshot mit lauter ``None``.

Und ``None`` heisst weiter unten ueberall "unauffaellig": swr=None wird
zu 1.0, battery_v=None gilt als Netzbetrieb, freq_hz=None laesst
Antennen- und Lizenz-Guard passieren, weil beide das als Startzustand
lesen. Der Wegfall des Rigs war damit kein Sperrgrund, sondern liess
jede rig-seitige Pruefung durchwinken.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.config import (
    AntennaConfig,
    AppConfig,
    BandConfig,
    OperatingConfig,
    OperatorConfig,
)
from ft8_appliance.rig.rigctld_client import RigctldClient, RigSnapshot
from ft8_appliance.runtime import FakeSlotClock, Orchestrator
from ft8_appliance.statemachine.guards import (
    DEFAULT_GUARDS,
    GuardLimits,
    HardwareState,
    evaluate,
    first_failure,
    rig_link_guard,
)

# --------------------------------------------------------- der Ausgangsbefund


@pytest.mark.asyncio
async def test_a_dead_rigctld_yields_an_all_none_snapshot() -> None:
    """Grundlage des ganzen Fixes: kein Fehler, nur leere Felder.

    Wuerde snapshot() bei totem rigctld werfen, faenge der Poll-Loop das
    ab und _last_rig bliebe schlicht stehen — ein anderer Fehlerfall.
    Diese Erwartung also hier festnageln, sonst zielt der Fix ins Leere.
    """
    client = RigctldClient(host="127.0.0.1", port=45999, timeout=0.3)
    snap = await client.snapshot()  # darf nicht werfen
    assert snap.freq_hz is None
    assert snap.swr is None
    assert snap.battery_v is None


def test_the_other_guards_all_pass_on_an_empty_snapshot() -> None:
    """Dokumentiert, warum es einen eigenen Guard braucht: die
    None-Werte eines toten Rigs sind fuer jeden bestehenden Guard
    unauffaellig."""
    from ft8_appliance.statemachine.guards import (
        antenna_guard,
        battery_guard,
        license_guard,
        swr_guard,
    )
    # So uebersetzt der Orchestrator einen Leer-Snapshot.
    hw = HardwareState(swr=1.0, battery_v=None)
    for guard in (swr_guard, battery_guard, antenna_guard, license_guard):
        assert guard(hw, GuardLimits()).ok is True


# ------------------------------------------------------------------ der Guard


def test_a_lost_rig_link_blocks_tx() -> None:
    res = rig_link_guard(
        HardwareState(rig_link_age_s=120.0), GuardLimits(rig_link_max_age_s=60.0),
    )
    assert res.ok is False
    assert res.code == "guard.rig_link"


def test_a_fresh_link_passes() -> None:
    assert rig_link_guard(
        HardwareState(rig_link_age_s=1.0), GuardLimits(rig_link_max_age_s=60.0),
    ).ok is True


def test_short_dropouts_are_tolerated() -> None:
    """rigctld-Neustart und USB-Re-Enumeration dauern Sekunden. Der Lock
    ist sticky — wuerde er dabei greifen, muesste Sebastian jeden
    Schluckauf von Hand quittieren."""
    for age in (0.0, 5.0, 30.0, 59.9):
        assert rig_link_guard(
            HardwareState(rig_link_age_s=age), GuardLimits(rig_link_max_age_s=60.0),
        ).ok is True


def test_never_having_seen_a_snapshot_does_not_block() -> None:
    """None heisst "seit dem Start nie einer angekommen" — Bootphase,
    Demo-Betrieb, Testpfade ohne Rig. Daraus laesst sich kein Verlust
    ableiten, und der Lock waere sticky."""
    assert rig_link_guard(HardwareState(rig_link_age_s=None), GuardLimits()).ok is True


def test_the_guard_runs_in_the_default_pipeline() -> None:
    """Sonst existiert er nur auf dem Papier."""
    assert rig_link_guard in DEFAULT_GUARDS


def test_a_lost_link_is_the_reported_reason_not_something_downstream() -> None:
    """Die Reihenfolge in DEFAULT_GUARDS entscheidet, was der Operator
    aufs Handy bekommt. "SWR zu hoch" bei abgezogenem USB-Kabel schickt
    ihn an die Antenne statt ans Kabel."""
    hw = HardwareState(rig_link_age_s=300.0, swr=5.0)
    failure = first_failure(evaluate(hw, GuardLimits()))
    assert failure is not None
    assert failure.name == "rig_link_guard"


# ------------------------------------------------- Verdrahtung im Orchestrator


def test_the_config_limit_reaches_the_guard() -> None:
    from ft8_appliance.config.models import OperatingConfig

    op = OperatingConfig()
    lim = GuardLimits(rig_link_max_age_s=op.rig_link_max_age_s)
    assert rig_link_guard(HardwareState(rig_link_age_s=op.rig_link_max_age_s + 1),
                          lim).ok is False


class _StopLoop(Exception):
    """Sentinel, um den ``while True``-Poll-Loop kontrolliert zu verlassen."""


def _orch(snapshots: list[RigSnapshot]) -> Orchestrator:
    """Echter Orchestrator, nur Rig/GPS/Decoder gemockt."""
    rig = AsyncMock()
    rig.snapshot = AsyncMock(side_effect=list(snapshots))
    rig.close = AsyncMock(return_value=None)
    gps = AsyncMock()
    gps.snapshot = type("S", (), {"mode": 3, "lat": 0, "lon": 0, "ts": None,
                                  "lock_for_min": None, "satellites_used": None})()
    gps.close = AsyncMock(return_value=None)

    async def _no_decodes(tick):
        return []

    cfg = AppConfig(
        operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
        bands=[BandConfig(name="20m", freq_khz=14074, antenna="endfed")],
        antennas=[AntennaConfig(name="endfed", bands=["20m"])],
        operating=OperatingConfig(),
    )
    return Orchestrator(config=cfg, rig=rig, gps=gps,
                        decode_source=_no_decodes, slot_clock=FakeSlotClock(count=0))


async def _poll_n(orch: Orchestrator, n: int, monkeypatch) -> None:
    """Den ECHTEN _rig_poll_loop n Runden laufen lassen.

    Bewusst nicht die zwei relevanten Zeilen im Test nachbauen — das
    pruefte nur die Kopie, nicht den Produktionspfad.
    """
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


@pytest.mark.asyncio
async def test_the_poll_loop_stamps_freshness_on_a_usable_snapshot(monkeypatch) -> None:
    orch = _orch([RigSnapshot(freq_hz=14_074_000)] * 3)
    assert orch._last_rig_at is None  # vor dem ersten Poll: kein Lebenszeichen
    await _poll_n(orch, 2, monkeypatch)
    assert orch._last_rig_at is not None


@pytest.mark.asyncio
async def test_an_empty_snapshot_is_not_a_sign_of_life(monkeypatch) -> None:
    """Der Kern der Verdrahtung: gestempelt wird nur, wenn wenigstens die
    Frequenz gelesen werden konnte. Zaehlte jeder zurueckgekehrte
    Snapshot als Lebenszeichen, bliebe das Alter bei totem rigctld ewig
    bei 0 — der Guard feuerte nie, denn genau dann kommen ja Snapshots
    zurueck, nur eben leere.
    """
    good = RigSnapshot(freq_hz=14_074_000)
    orch = _orch([good, RigSnapshot(), RigSnapshot(), RigSnapshot()])
    await _poll_n(orch, 1, monkeypatch)
    stamped = orch._last_rig_at
    assert stamped is not None

    await _poll_n(orch, 2, monkeypatch)  # ab jetzt nur noch Leer-Snapshots
    assert orch._last_rig_at == stamped  # Stempel darf NICHT nachgezogen werden


@pytest.mark.asyncio
async def test_the_age_reaches_the_hardware_state(monkeypatch) -> None:
    """Ohne diese Verdrahtung bliebe rig_link_age_s auf dem Default None
    und der Guard waere trotz Stempel dauerhaft inert."""
    import time as _time
    from datetime import UTC, datetime

    from ft8_appliance.runtime.slot_clock import SlotTick

    orch = _orch([RigSnapshot(freq_hz=14_074_000)] * 3)
    now = datetime.now(UTC)
    tick = SlotTick(index=0, posix=now.timestamp(), utc_start=now)

    await orch._refresh_hardware_state(tick)
    # 2026-09-08: "noch nie gepollt" ist nicht mehr None, sondern das Alter seit
    # Start — sonst bleibt der Guard gruen, wenn rigctld gar nicht erst hochkommt.
    age = orch._hardware_state.rig_link_age_s
    assert age is not None and age < 5.0  # frisch gestartet -> Guard noch gruen

    orch._last_rig_at = _time.monotonic() - 300.0  # Rig seit 5 min weg
    await orch._refresh_hardware_state(tick)
    age = orch._hardware_state.rig_link_age_s
    assert age is not None
    assert age == pytest.approx(300.0, abs=5.0)
    assert rig_link_guard(orch._hardware_state, GuardLimits()).ok is False


def test_never_connected_locks_after_the_grace_period() -> None:
    """2026-09-08: rigctld war nach einem Stromreset disabled und kam nie hoch.
    rig_link_age_s blieb None, der Guard liess durch, und die Box rief CQ ohne
    PTT-Steuerung. Jetzt meldet der Orchestrator "Alter seit Start"."""
    from ft8_appliance.statemachine.guards import GuardLimits, HardwareState, rig_link_guard

    lim = GuardLimits(rig_link_max_age_s=60.0)
    # Bootphase: erst wenige Sekunden ohne Rig -> noch gruen
    assert rig_link_guard(HardwareState(rig_link_age_s=5.0), lim).ok is True
    # rigctld kommt nicht hoch -> nach der Schwelle gesperrt
    assert rig_link_guard(HardwareState(rig_link_age_s=1800.0), lim).ok is False
    # Demo/Tests ohne Rig setzen das Feld nicht
    assert rig_link_guard(HardwareState(rig_link_age_s=None), lim).ok is True


@pytest.mark.asyncio
async def test_orchestrator_reports_age_since_start_when_rig_never_answered() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from ft8_appliance.config import AntennaConfig, AppConfig, BandConfig, OperatingConfig, OperatorConfig
    from ft8_appliance.runtime import FakeSlotClock, Orchestrator
    from ft8_appliance.runtime.slot_clock import SlotTick

    cfg = AppConfig(
        operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
        bands=[BandConfig(name="20m", freq_khz=14074, antenna="w")],
        antennas=[AntennaConfig(name="w", bands=["20m"])],
        operating=OperatingConfig(),
    )
    rig = AsyncMock(); rig.snapshot = AsyncMock(side_effect=OSError("rigctld down")); rig.close = AsyncMock()
    gps = AsyncMock(); gps.snapshot = SimpleNamespace(mode=3, lat=0, lon=0, ts=None, lock_for_min=None, satellites_used=None); gps.close = AsyncMock()

    async def nd(tick):
        return []

    o = Orchestrator(config=cfg, rig=rig, gps=gps, decode_source=nd, slot_clock=FakeSlotClock(count=0))
    o._rig_watch_since -= 3600.0            # eine Stunde ohne je eine Rig-Antwort
    posix = 1_700_000_015.0
    await o._refresh_hardware_state(SlotTick(index=1, posix=posix,
                                             utc_start=_dt.datetime.fromtimestamp(posix, tz=_dt.UTC)))
    assert o._hardware_state.rig_link_age_s is not None
    assert o._hardware_state.rig_link_age_s > 3000
