"""End-to-end integration tests: Orchestrator over the real clients
talking to the in-process mocks.

Covers:
  * Boot path: connect rigctld + gpsd, run a few simulated slots
  * Status snapshot reflects mock state
  * Decode-driven QSO sequence from CQ to RR73, log action emitted
  * Panic-stop turns off PTT on the mock rig
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ft8_appliance.config import (
    AntennaConfig,
    AppConfig,
    BandConfig,
    OperatingConfig,
    OperatorConfig,
)
from ft8_appliance.gps import GpsdClient
from ft8_appliance.rig import RigctldClient
from ft8_appliance.runtime import FakeSlotClock, Orchestrator, SlotTick
from ft8_appliance.statemachine import DecodedMsg
from tests.mocks.mock_gpsd import MockGpsd
from tests.mocks.mock_rigctld import MockRigctld


def _cfg() -> AppConfig:
    return AppConfig(
        operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
        bands=[BandConfig(name="20m", freq_khz=14074, antenna="endfed_2040")],
        antennas=[AntennaConfig(name="endfed_2040", bands=["20m"])],
        operating=OperatingConfig(),
    )


def _decode(call_from: str | None, call_to: str | None, message: str,
            grid: str | None = None, snr: int = -10) -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC),
        call_from=call_from,
        call_to=call_to,
        grid=grid,
        message=message,
        snr_db=snr,
        dt_s=0.2,
        freq_offset_hz=1500,
        band="20m",
    )


class ScriptedDecodeSource:
    """Decode source that returns a queued list per slot in order."""

    def __init__(self, scripts: list[list[DecodedMsg]]) -> None:
        self.scripts = scripts
        self.index = 0

    async def __call__(self, tick: SlotTick) -> list[DecodedMsg]:
        if self.index < len(self.scripts):
            out = self.scripts[self.index]
        else:
            out = []
        self.index += 1
        return out


# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_orchestrator_boots_and_reports_status() -> None:
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(),
            rig=rig,
            gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=1),
        )
        await orch.start()
        # Deterministisch auf den ersten Rig-Poll + GPS-Fix warten statt
        # fixem sleep (timing-robust): bis freq + GPS da sind, max ~5 s.
        snap = orch.status()
        for _ in range(50):
            await asyncio.sleep(0.1)
            snap = orch.status()
            if snap.rig.freq_hz is not None and snap.gps.mode == 3:
                break
        assert snap.callsign == "DK9XR"
        assert snap.state == "IDLE"
        assert snap.rig.freq_hz == 14_074_000
        assert snap.gps.mode == 3
        await orch.stop()


@pytest.mark.asyncio
async def test_orchestrator_start_cq_emits_actions() -> None:
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(),
            rig=rig,
            gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),  # no automatic ticks
        )
        await orch.start()
        await asyncio.sleep(0.2)  # let gpsd populate snapshot
        await orch.handle_start_cq()
        assert orch.status().state == "CQ_CALLING"
        # The TX_MESSAGE action should have run through the dispatcher.
        # We don't assert wire details — that's for the audio-pipeline phase.
        await orch.stop()


@pytest.mark.asyncio
async def test_orchestrator_full_qso_via_scripted_decodes() -> None:
    """Drive 4 slots manually after gpsd has settled — CQ → answer → report → RR73 → IDLE."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        scripts: list[list[DecodedMsg]] = [
            [],  # slot 0: nobody answered our CQ yet
            [_decode("W1AW", "DK9XR", "DK9XR W1AW FN31", grid="FN31", snr=-8)],
            [_decode("W1AW", "DK9XR", "DK9XR W1AW -12", snr=-12)],
            [_decode("W1AW", "DK9XR", "DK9XR W1AW RR73")],
        ]
        decode_src = ScriptedDecodeSource(scripts)
        orch = Orchestrator(
            config=_cfg(),
            rig=rig,
            gps=gps,
            decode_source=decode_src,
            slot_clock=FakeSlotClock(count=0),  # don't auto-fire
        )
        await orch.start()
        await asyncio.sleep(0.2)  # wait for gpsd's first TPV
        # gpsd live-snapshot must be populated; cached HardwareState only
        # gets a fresh copy when handle_start_cq() runs its refresh.
        assert orch.gps.snapshot.mode == 3, "gpsd snapshot didn't populate"

        await orch.handle_start_cq()
        assert orch.status().state == "CQ_CALLING"

        # Drive 4 slots by hand
        for i in range(4):
            await orch.process_slot(SlotTick(
                index=i,
                posix=1_700_000_000.0 + i * 15,
                utc_start=datetime.fromtimestamp(1_700_000_000.0 + i * 15, tz=UTC),
            ))

        snap = orch.status()
        # WSJT-Z-style auto_cq: pressing CQ enables a loopback so the
        # machine returns to CQ_CALLING after each logged QSO. Stop is
        # the explicit way out — see state_machine.on_user_start_cq.
        assert snap.state == "CQ_CALLING", \
            f"expected CQ_CALLING after auto-cq loopback, got {snap.state}"
        assert any(a.kind == "LOG_QSO" for a in orch._action_log)
        log_action = next(a for a in orch._action_log if a.kind == "LOG_QSO")
        assert log_action.payload["call"] == "W1AW"
        assert log_action.payload["grid_rcvd"] == "FN31"
        await orch.stop()


@pytest.mark.asyncio
async def test_orchestrator_panic_turns_off_ptt_on_rig() -> None:
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(),
            rig=rig,
            gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
        )
        await orch.start()
        await asyncio.sleep(0.2)
        # Simulate rig PTT already on
        mock_rig.state.ptt = True
        await orch.handle_panic()
        # Mock should now show PTT off
        assert mock_rig.state.ptt is False
        assert orch.status().state == "IDLE"
        await orch.stop()


@pytest.mark.asyncio
async def test_orchestrator_decode_subscriber_receives_pushes() -> None:
    """SSE subscribers should see every decode the slot loop pushes."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        scripts = [
            [_decode("AA1A", None, "CQ AA1A FN42", grid="FN42")],
            [_decode("BB2B", None, "CQ BB2B AA00", grid="AA00")],
        ]
        orch = Orchestrator(
            config=_cfg(),
            rig=rig,
            gps=gps,
            decode_source=ScriptedDecodeSource(scripts),
            slot_clock=FakeSlotClock(count=0),  # manual drive
        )
        await orch.start()
        await asyncio.sleep(0.2)  # let gpsd settle
        sub = orch.subscribe_decodes()
        for i in range(2):
            await orch.process_slot(SlotTick(
                index=i,
                posix=1_700_000_000.0 + i * 15,
                utc_start=datetime.fromtimestamp(1_700_000_000.0 + i * 15, tz=UTC),
            ))
        received = []
        while not sub.empty():
            received.append(sub.get_nowait())
        assert len(received) >= 2
        assert {d.call_from for d in received} == {"AA1A", "BB2B"}
        await orch.stop()


# ---------------------------------------------------------------------------
# PI-Regler-Tests: ALC als Hauptregelgroesse mit pwr_meter-Fallback.
#
# Architektur (Sebastian + Claude 2026-05-22 Abend, nach Live-Daten):
#  Die Strecke gain → pwr_meter ist im Sweet-Spot-Bereich (gain 0.25..0.35)
#  fast flach (pwr 0.43..0.45). Die Strecke gain → ALC ist hier exzellent
#  monoton (3..35 %). Deshalb regeln wir auf ALC, mit pwr_meter NUR fuer
#  die Disambiguation "alc=0 = Sweet-Spot oder Underdrive?".
#
# Regime-Tabelle (pro Burst, in dieser Reihenfolge):
#  1. alc_peak > alc_safety_threshold  → Watchdog cut, return
#  2. alc_peak == 0 UND pwr_peak == 0  → Sensor-Sync-Glitch, skip
#  3. alc_peak > 0                     → ALC-Regime, PI auf alc_target_pct
#  4. alc_peak == 0 UND pwr < threshold→ PWR-Regime, PI auf pwr_target_ratio
#  5. alc_peak == 0 UND pwr >= threshold→ Sweet-Spot, kein Update
#
# Defaults (siehe OperatingConfig):
#  Kp=0.2, Ki=0.02, alc_target_pct=15, alc_deadband_pct=5,
#  pwr_target_ratio=0.80, alc_safety_threshold=40, alc_safety_factor=0.7.
def _drive_burst(
    orch,
    alc_values: list[float],
    pwr_meter: float | None = None,
    pwr_norm: float | None = None,
) -> None:
    """Simuliert einen TX-Burst: PTT-on, N Samples, PTT-off."""
    from ft8_appliance.rig.rigctld_client import RigSnapshot
    orch._last_rig = RigSnapshot(
        ptt=True, alc=alc_values[0],
        rfpower_meter=pwr_meter, rfpower_norm=pwr_norm,
    )
    orch._apply_alc_closed_loop()
    for v in alc_values:
        orch._last_rig = RigSnapshot(
            ptt=True, alc=v,
            rfpower_meter=pwr_meter, rfpower_norm=pwr_norm,
        )
        orch._apply_alc_closed_loop()
    orch._last_rig = RigSnapshot(
        ptt=False, alc=None,
        rfpower_meter=pwr_meter, rfpower_norm=pwr_norm,
    )
    orch._apply_alc_closed_loop()


@pytest.mark.asyncio
async def test_alc_safety_watchdog_cuts_gain_on_high_alc() -> None:
    """Safety-Overlay: peak ALC > threshold → gain × factor, Integrator reset."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(), rig=rig, gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
        )
        await orch.start()
        orch._audio_gain = 0.5
        orch._pwr_integrator = 0.3
        # Burst-Peak ALC = 50 > threshold 40
        _drive_burst(orch, [0.30, 0.50, 0.45], pwr_meter=0.45, pwr_norm=0.5)
        assert orch._audio_gain == pytest.approx(0.5 * 0.7, abs=1e-6), \
            f"Watchdog cut erwartet (0.5 × 0.7 = 0.35), got {orch._audio_gain}"
        assert orch._pwr_integrator == 0.0, "Integrator muss resettet sein"
        assert orch._last_alc_pct == 50
        await orch.stop()


@pytest.mark.asyncio
async def test_alc_regime_drives_gain_up_when_alc_below_target() -> None:
    """ALC-Regime, Up-Step: alc_peak < alc_target_pct − deadband
    → positiver Fehler → gain rauf. Defaults target=15 deadband=5 → unter
    10 triggert Up-Step."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(), rig=rig, gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
        )
        await orch.start()
        orch._audio_gain = 0.20
        orch._pwr_integrator = 0.0
        # ALC=3 % (unter Deadband 10), pwr=0.25 (deutlich unter underdrive_
        # threshold 0.40) → echter Underdrive, Up-Step soll greifen.
        # target=0.15, pv=0.03, e=+0.12
        # Δu = 0.2*0.12 + 0.02*0.12 = 0.0264 → gain ≈ 0.226
        _drive_burst(orch, [0.02, 0.03, 0.025], pwr_meter=0.25, pwr_norm=0.5)
        expected = 0.20 + (0.2 + 0.02) * (0.15 - 0.03)
        assert orch._audio_gain == pytest.approx(expected, abs=1e-3), \
            f"ALC-Up-Step erwartet {expected:.3f}, got {orch._audio_gain:.3f}"
        assert orch._pwr_integrator == pytest.approx(0.12, abs=1e-3)
        await orch.stop()


@pytest.mark.asyncio
async def test_alc_regime_drives_gain_down_when_alc_above_target() -> None:
    """ALC-Regime, Down-Step: alc_peak > alc_target_pct + deadband (=20),
    aber unter Watchdog-Schwelle 40 → negativer Fehler → gain runter."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(), rig=rig, gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
        )
        await orch.start()
        orch._audio_gain = 0.35
        orch._pwr_integrator = 0.0
        # ALC=30 % (ueber 20 = target+deadband, unter 40 = Watchdog)
        # target=0.15, pv=0.30, e=-0.15
        # Δu = 0.2*(-0.15) + 0.02*(-0.15) = -0.033 → gain ≈ 0.317
        _drive_burst(orch, [0.20, 0.30, 0.28], pwr_meter=0.45, pwr_norm=0.5)
        expected = 0.35 + (0.2 + 0.02) * (0.15 - 0.30)
        assert orch._audio_gain == pytest.approx(expected, abs=1e-3), \
            f"ALC-Down-Step erwartet {expected:.3f}, got {orch._audio_gain:.3f}"
        await orch.stop()


@pytest.mark.asyncio
async def test_alc_regime_holds_in_deadband() -> None:
    """ALC-Deadband: alc_peak im [target − db, target + db] = [10, 20]
    → kein gain-Update, kein I-Aufbau."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(), rig=rig, gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
        )
        await orch.start()
        orch._audio_gain = 0.28
        orch._pwr_integrator = 0.0
        # ALC=15 % EXAKT am Setpoint, deutlich im Deadband
        _drive_burst(orch, [0.10, 0.15, 0.12], pwr_meter=0.44, pwr_norm=0.5)
        assert orch._audio_gain == 0.28, \
            f"gain im Deadband soll stabil bleiben, got {orch._audio_gain}"
        assert orch._pwr_integrator == 0.0, \
            f"I soll bei e in Deadband nicht aufgebaut werden, got {orch._pwr_integrator}"
        await orch.stop()


@pytest.mark.asyncio
async def test_sweet_spot_alc_zero_with_full_power_holds() -> None:
    """Sweet-Spot: alc=0 UND pwr_meter >= pwr_target_ratio * pwr_norm
    → kein Update (Limiter inaktiv = perfekter FT8-Arbeitspunkt).

    DAS ist der zentrale Test fuer den Re-Design — vorher wurde alc=0
    als Underdrive interpretiert und der Loop drehte hoch, obwohl der
    Rig schon volle Leistung lieferte."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(), rig=rig, gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
        )
        await orch.start()
        orch._audio_gain = 0.26
        orch._pwr_integrator = 0.0
        # alc=0 UND pwr=0.44 (>= 0.80 * 0.5 = 0.40) → Sweet-Spot
        for _ in range(5):
            _drive_burst(orch, [0.0, 0.0, 0.0], pwr_meter=0.44, pwr_norm=0.5)
        assert orch._audio_gain == 0.26, \
            f"Sweet-Spot soll stabil bleiben, gain wanderte zu {orch._audio_gain}"
        assert orch._pwr_integrator == 0.0
        await orch.stop()


@pytest.mark.asyncio
async def test_sweet_spot_with_low_alc_but_full_power_holds() -> None:
    """Erweiterte Sweet-Spot-Erkennung: alc < target − deadband (also
    Up-Step waere faellig) ABER pwr_meter ist schon >= underdrive_threshold
    → KEIN Up-Step (der Rig laeuft schon nahe Nennleistung, niedrige
    ALC ist nur "Limiter inaktiv", nicht "Audio zu leise").

    Sebastian sah 2026-05-22 abends gain=0.27 mit pwr=0.43 und alc=3..5
    Bursts → unnoetige Up-Steps → Pendeln. Mit dem Fix bleibt System still."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(), rig=rig, gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
        )
        await orch.start()
        orch._audio_gain = 0.27
        orch._pwr_integrator = 0.0
        # alc=3 (klar unter Deadband 10), aber pwr=0.43 (>= 0.40 threshold)
        for _ in range(3):
            _drive_burst(orch, [0.02, 0.03, 0.02], pwr_meter=0.43, pwr_norm=0.5)
        assert orch._audio_gain == 0.27, \
            f"Sweet-Spot mit alc=3 aber pwr=full → gain still, drifted to {orch._audio_gain}"
        assert orch._pwr_integrator == 0.0
        await orch.stop()


@pytest.mark.asyncio
async def test_down_step_still_active_even_at_full_power() -> None:
    """Komplement: Down-Step muss IMMER greifen, auch wenn pwr_meter
    am Setting ist — Splatter-Schutz ist unabhaengig vom Power-Status."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(), rig=rig, gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
        )
        await orch.start()
        orch._audio_gain = 0.30
        orch._pwr_integrator = 0.0
        # alc=28 (ueber Deadband 20), pwr=0.45 (am Setting) → Down trotzdem
        _drive_burst(orch, [0.20, 0.28, 0.25], pwr_meter=0.45, pwr_norm=0.5)
        assert orch._audio_gain < 0.30, \
            f"Down-Step muss trotz pwr=full greifen, gain blieb {orch._audio_gain}"
        await orch.stop()


@pytest.mark.asyncio
async def test_pwr_regime_rate_limited_on_big_error() -> None:
    """PWR-Rate-Limiter: bei grossem error wird Δgain auf op.pwr_regime_max_delta
    beschnitten, damit eine Audio-Bandpass-Sequence (262 Hz Reply,
    Sebastian sah 2026-05-22) nicht in 3 Bursts in den Watchdog rennt."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(), rig=rig, gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
        )
        await orch.start()
        orch._audio_gain = 0.27
        orch._pwr_integrator = 0.0
        # alc=0, pwr=0.10 → PWR-Regime, error=+0.30
        # Ohne Rate-Limit: Δu = 0.2*0.30 + 0.02*0.30 = 0.066 → gain → 0.336
        # Mit Rate-Limit 0.03: gain → 0.30 (max)
        _drive_burst(orch, [0.0, 0.0, 0.0], pwr_meter=0.10, pwr_norm=0.5)
        assert orch._audio_gain == pytest.approx(0.27 + 0.03, abs=1e-3), \
            f"Rate-Limit muss greifen, got {orch._audio_gain}"
        await orch.stop()


@pytest.mark.asyncio
async def test_pwr_underdrive_regime_kicks_in_when_alc_zero_and_low_power() -> None:
    """PWR-Underdrive-Regime: alc=0 UND pwr_meter < pwr_target_ratio * pwr_norm
    → PI auf pwr_meter (Cold-Start, echter Underdrive)."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(), rig=rig, gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
        )
        await orch.start()
        orch._audio_gain = 0.10
        orch._pwr_integrator = 0.0
        # alc=0, pwr=0.10 (<< 0.40 threshold) → PWR-Regime
        # PI wuerde gerne Δu = 0.2*0.30 + 0.02*0.30 = 0.066, aber
        # Rate-Limit pwr_regime_max_delta=0.03 beschneidet auf 0.03.
        _drive_burst(orch, [0.0, 0.0, 0.0], pwr_meter=0.10, pwr_norm=0.5)
        expected = 0.10 + 0.03  # Rate-Limit greift
        assert orch._audio_gain == pytest.approx(expected, abs=1e-3), \
            f"PWR-Up-Step rate-limited erwartet {expected:.3f}, got {orch._audio_gain:.3f}"
        assert orch._audio_gain > 0.10, "Cold-Start muss gain anheben"
        await orch.stop()


@pytest.mark.asyncio
async def test_pi_anti_windup_at_saturation() -> None:
    """Anti-Windup: gain am Saturation-Limit + Fehler in gleicher Richtung
    → Integrator akkumuliert NICHT weiter."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(), rig=rig, gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
        )
        await orch.start()
        orch._audio_gain = 1.0
        orch._pwr_integrator = 0.2
        # alc=0 + pwr=0.10 → PWR-Regime, error positiv → "will hoch", aber gain klemmt
        _drive_burst(orch, [0.0, 0.0, 0.0], pwr_meter=0.10, pwr_norm=0.5)
        assert orch._pwr_integrator == pytest.approx(0.2, abs=1e-6), \
            f"Integrator soll bei Saturation gehalten werden, got {orch._pwr_integrator}"
        assert orch._audio_gain == 1.0, "gain klemmt am Max"
        await orch.stop()


@pytest.mark.asyncio
async def test_pi_skips_burst_on_sensor_sync_glitch() -> None:
    """Sensor-Sync-Gate: alc=0 UND pwr=0 → Hamlib-Glitch, skip."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(), rig=rig, gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
        )
        await orch.start()
        orch._audio_gain = 0.26
        orch._pwr_integrator = 0.0
        for _ in range(3):
            _drive_burst(orch, [0.0, 0.0, 0.0], pwr_meter=0.0, pwr_norm=0.5)
        assert orch._audio_gain == 0.26
        assert orch._pwr_integrator == 0.0
        await orch.stop()


@pytest.mark.asyncio
async def test_alc_deadband_blocks_p_anteil_drift() -> None:
    """Sebastian sah 2026-05-22 abends dass der reine I-Deadband nicht
    reichte: bei alc=11 (e=+0.04, im Deadband) triggerte Kp*e=+0.008
    immer noch Updates → 3 Up-Steps in 5 min bis Setpoint genau getroffen
    war. Fix: Deadband ist absolutes Leave-Alone (kein P, kein I, kein
    Update). Im Soll-Fenster steht das System still."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(), rig=rig, gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
        )
        await orch.start()
        orch._audio_gain = 0.27
        orch._pwr_integrator = 0.0
        # alc=11 % → e = 0.04 (in Deadband). Mit Kp=0.2 waere Δu=+0.008.
        # Deadband-Skip muss verhindern dass DAS einen gain-Update macht.
        for _ in range(5):
            _drive_burst(orch, [0.10, 0.11, 0.10], pwr_meter=0.43, pwr_norm=0.5)
        assert orch._audio_gain == 0.27, \
            f"P-Anteil im Deadband darf gain NICHT bewegen, drifted to {orch._audio_gain}"
        assert orch._pwr_integrator == 0.0
        await orch.stop()


@pytest.mark.asyncio
async def test_alc_i_deadband_prevents_drift() -> None:
    """I-Deadband: bei |e| < alc_deadband bleibt der Integrator konstant."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(), rig=rig, gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
        )
        await orch.start()
        orch._audio_gain = 0.28
        orch._pwr_integrator = -0.08
        I_before = orch._pwr_integrator
        # alc=14 % (|e| = |0.15 - 0.14| = 0.01, im Deadband 0.05)
        for _ in range(5):
            _drive_burst(orch, [0.10, 0.14, 0.12], pwr_meter=0.44, pwr_norm=0.5)
        assert orch._pwr_integrator == pytest.approx(I_before, abs=1e-6), \
            f"I soll im Deadband konstant bleiben, drifted to {orch._pwr_integrator}"
        await orch.stop()


@pytest.mark.asyncio
async def test_loop_status_surfaces_alc_to_ui() -> None:
    """Smoke: _last_alc_pct wird im Burst gesetzt und im Status-Snapshot
    sichtbar (UI-Anzeige fuer den Operator)."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(), rig=rig, gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
        )
        await orch.start()
        _drive_burst(orch, [0.10, 0.18, 0.15], pwr_meter=0.40, pwr_norm=0.5)
        snap = orch.status()
        assert snap.last_alc_pct == 18, f"UI soll Peak-ALC anzeigen, got {snap.last_alc_pct}"
        assert snap.audio_gain == orch._audio_gain
        await orch.stop()


@pytest.mark.asyncio
async def test_config_hot_reload_propagates_active_antenna() -> None:
    """Regression test for the First-Boot-Wizard antenna bug.

    The wizard wrote a new config with a renamed antenna ("main"), but
    the StatusBar kept showing the old antenna ("endfed_2040") because
    save_config only updated the module-level _current singleton — the
    orchestrator's _active_antenna was set at boot from the *old* config
    and never invalidated. Fixed by calling on_config_changed from the
    save_config endpoint; this test pins that wiring.
    """
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(),
            rig=rig,
            gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
            db_enabled=False,
        )
        await orch.start()
        # Boot picks up the first antenna from config.
        assert orch._active_antenna == "endfed_2040"

        # Operator runs First-Boot-Wizard and saves a config with a
        # renamed antenna. The old one no longer exists.
        new_cfg = AppConfig(
            operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
            bands=[BandConfig(name="20m", freq_khz=14074, antenna="main")],
            antennas=[AntennaConfig(name="main", bands=["20m"])],
            operating=OperatingConfig(),
        )
        await orch.on_config_changed(new_cfg)

        # Active antenna must fall back to the only one in the new config,
        # NOT stay on the deleted "endfed_2040".
        assert orch._active_antenna == "main", \
            f"hot-reload didn't update _active_antenna, got {orch._active_antenna!r}"
        # Status snapshot agrees (no stale data in the SSE/StatusBar path).
        assert orch.status().active_antenna == "main"
        await orch.stop()


@pytest.mark.asyncio
async def test_multi_color_grid_helpers() -> None:
    """is_new_grid / is_new_grid_on_band track 4-char grid + grid-band tuples."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(),
            rig=rig,
            gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
            db_enabled=False,  # we call _do_log_qso directly; skip DB I/O
        )
        await orch.start()
        # Fresh state: every grid is new
        assert orch.is_new_grid("FN31") is True
        assert orch.is_new_grid("FN31aa") is True  # 6-char form, canonicalised
        assert orch.is_new_grid_on_band("FN31", "20m") is True
        # Empty / None inputs default to False (not "new")
        assert orch.is_new_grid(None) is False
        assert orch.is_new_grid("") is False
        assert orch.is_new_grid_on_band("FN31", None) is False

        # Simulate logging a QSO with grid FN31 on 20m
        await orch._do_log_qso({
            "call": "W1AW", "band": "20m", "grid_rcvd": "FN31",
            "rst_sent": -7, "rst_rcvd": -12,
            "qso_start": datetime.now(UTC), "qso_end": datetime.now(UTC),
            "freq_offset_hz": 1500,
        })
        # Now FN31 is known overall and known on 20m
        assert orch.is_new_grid("FN31") is False
        assert orch.is_new_grid_on_band("FN31", "20m") is False
        # But FN31 is still "new on 40m" (band-fill detector)
        assert orch.is_new_grid_on_band("FN31", "40m") is True
        # FN42 is still new everywhere
        assert orch.is_new_grid("FN42") is True
        await orch.stop()


@pytest.mark.asyncio
async def test_alc_closed_loop_stays_put_inside_window() -> None:
    """Within the [low, high] target window the loop leaves gain alone —
    prevents oscillation around the setpoint."""
    async with MockRigctld() as mock_rig, MockGpsd() as mock_gps:
        rig = RigctldClient(host="127.0.0.1", port=mock_rig.port)
        gps = GpsdClient(host="127.0.0.1", port=mock_gps.port)
        orch = Orchestrator(
            config=_cfg(),
            rig=rig,
            gps=gps,
            decode_source=ScriptedDecodeSource([]),
            slot_clock=FakeSlotClock(count=0),
        )
        await orch.start()
        baseline = orch._audio_gain
        # Burst-Peak 15% — zwischen default low=5 und high=25, also nichts zu tun.
        _drive_burst(orch, [0.10, 0.15, 0.12])
        assert orch._audio_gain == baseline
        assert orch._last_alc_pct == 15
        await orch.stop()


def test_blitzortung_radius_hot_reload_preserves_live_client() -> None:
    """Regression (Sebastian 2026-06-02): Radius-Aenderung via Config-Save
    muss den LAUFENDEN Blitzortung-Client erreichen.

    Bug war: on_config_changed → _init_integrations() baute den Client NEU,
    waehrend ws-Reader + Watchdog noch die alte Referenz (+ alten Radius)
    hielten → 30→10 km griff nie, man bekam weiter 25-km-Alarme.

    Fix: bestehenden Client weiterverwenden, nur Felder in-place updaten.
    """
    orch = Orchestrator(
        config=_cfg(),
        rig=RigctldClient(host="127.0.0.1", port=4533),
        gps=GpsdClient(host="127.0.0.1", port=2947),
        decode_source=ScriptedDecodeSource([]),
        slot_clock=FakeSlotClock(count=0),
    )
    orch._init_integrations()
    bz1 = orch.integrations.blitzortung
    assert bz1 is not None
    assert bz1.alarm_radius_km == 30
    # Live-State markieren um Objekt-Identitaet zu beweisen.
    bz1.total_strikes_seen = 7

    # Config-Save simulieren: Radius 30 → 10 (genau der gemeldete Fall).
    orch.config.integrations.blitzortung.alarm_radius_km = 10
    orch._init_integrations()  # das ruft der Hot-Reload-Pfad auf
    bz2 = orch.integrations.blitzortung

    assert bz2 is bz1, "Client muss WEITERVERWENDET werden (Loops halten die Referenz)"
    assert bz2.alarm_radius_km == 10, "Radius-Aenderung muss den Live-Client erreichen"
    assert bz2.total_strikes_seen == 7, "Strike-Buffer/State muss den Reload ueberleben"


@pytest.mark.asyncio
async def test_dx_cluster_hot_reload_reuses_unchanged_rebuilds_on_change() -> None:
    """Regression (Sebastian 2026-06-02): jeder Config-Save baute den
    DxClusterClient NEU + spawnte einen weiteren Reader-Task, OHNE den alten
    zu stoppen → Task-/Socket-Leak + Doppel-Login am Cluster.

    Fix: bei unveraenderten Settings den laufenden Client weiterverwenden
    (kein zweiter Reader); bei geaenderten Settings alten Reader stoppen,
    dann neuen bauen.
    """
    orch = Orchestrator(
        config=_cfg(),
        rig=RigctldClient(host="127.0.0.1", port=4533),
        gps=GpsdClient(host="127.0.0.1", port=2947),
        decode_source=ScriptedDecodeSource([]),
        slot_clock=FakeSlotClock(count=0),
    )
    orch._init_integrations()
    dxc1 = orch.integrations.dx_cluster
    assert dxc1 is not None

    # Unveraenderter Save → SELBES Objekt (kein neuer Reader, kein Leak).
    orch._init_integrations()
    assert orch.integrations.dx_cluster is dxc1, \
        "unveraenderte dx_cluster-Settings muessen den laufenden Client wiederverwenden"

    # Geaenderte Settings → neuer Client, alter wird gestoppt.
    orch.config.integrations.dx_cluster.host = "other.cluster.example"
    orch._init_integrations()
    await asyncio.sleep(0)  # stop()-Task durchlaufen lassen
    dxc3 = orch.integrations.dx_cluster
    assert dxc3 is not dxc1, "geaenderte Settings muessen einen neuen Client bauen"
    assert dxc3.host == "other.cluster.example"
