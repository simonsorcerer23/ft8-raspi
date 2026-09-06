"""Welle 2 des Fix-Plans zum Tiefenaudit 2026-09-06: messen statt vermuten.

* B1  TX-Start-Versatz zur Slot-Grenze messen, im Status zeigen und den
      Decoder zurueckschalten, wenn er den Sendestart zu spaet macht.
* B4  (neu, beim Bauen von B1 gefunden) Manuelle TX starten sofort, egal
      wo im Slot — das wird hier nur sichtbar gemacht, nicht geaendert.
* C5  Audio-Gain pro Rig-Modell persistieren.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.config import (
    AntennaConfig,
    AppConfig,
    BandConfig,
    OperatingConfig,
    OperatorConfig,
)
from ft8_appliance.config.models import RigConfig
from ft8_appliance.rig.rigctld_client import RigSnapshot
from ft8_appliance.runtime import FakeSlotClock, Orchestrator


def _cfg(rig_model: str = "ic7300", **operating) -> AppConfig:
    return AppConfig(
        operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
        bands=[BandConfig(name="20m", freq_khz=14074, antenna="endfed")],
        antennas=[AntennaConfig(name="endfed", bands=["20m"])],
        operating=OperatingConfig(**operating),
        rig=RigConfig(model=rig_model),
    )


def _orch(cfg: AppConfig | None = None, tmp_path=None) -> Orchestrator:
    rig = AsyncMock()
    rig.snapshot = AsyncMock(return_value=RigSnapshot(freq_hz=14_074_000))
    rig.close = AsyncMock(return_value=None)
    gps = AsyncMock()
    gps.snapshot = type("S", (), {"mode": 3, "lat": 0, "lon": 0, "ts": None,
                                  "lock_for_min": None, "satellites_used": None})()
    gps.close = AsyncMock(return_value=None)

    async def _no_decodes(tick):
        return []

    orch = Orchestrator(config=cfg or _cfg(), rig=rig, gps=gps,
                        decode_source=_no_decodes, slot_clock=FakeSlotClock(count=0))
    # status() liest zwei Attribute, die erst start() anlegt — wir starten
    # hier bewusst keine Loops.
    orch._rx_audio_dbfs_peak = None
    orch._rx_audio_dbfs_peak_ts = 0.0
    if tmp_path is not None:
        orch._runtime_state_path = tmp_path / "runtime_state.json"
    return orch


async def _tx(orch: Orchestrator, phase_s: float, *, in_slot: bool) -> None:
    """Einen TX durch _do_tx_message schicken, mit vorgegebener Slot-Phase."""
    orch._slot_phase_s = lambda: phase_s  # type: ignore[method-assign]
    orch._in_slot_tick = in_slot
    await orch._do_tx_message({"message": "CQ DK9XR JN58", "freq_offset_hz": 1500,
                               "kind": "cq"})


# ================================================================== B1 Messung


@pytest.mark.asyncio
async def test_tx_start_offset_is_recorded_and_exposed() -> None:
    orch = _orch()
    assert orch.status().tx_start_offset_s is None  # noch kein TX
    await _tx(orch, 0.72, in_slot=True)
    st = orch.status()
    assert st.tx_start_offset_s == pytest.approx(0.72)
    assert st.tx_start_offset_avg_s == pytest.approx(0.72)


@pytest.mark.asyncio
async def test_tx_start_offset_keeps_a_rolling_window_of_ten() -> None:
    orch = _orch()
    for i in range(14):
        await _tx(orch, 0.1 * (i + 1), in_slot=True)
    assert len(orch._tx_start_offsets_s) == 10
    assert orch.status().tx_start_offset_s == pytest.approx(1.4)


@pytest.mark.asyncio
async def test_status_route_passes_the_offset_through() -> None:
    from ft8_appliance.web.routes.status import StatusResponse
    assert "tx_start_offset_s" in StatusResponse.model_fields
    assert "tx_start_offset_avg_s" in StatusResponse.model_fields


# ================================================================== B1 Fallback


@pytest.mark.asyncio
async def test_three_late_slot_driven_tx_switch_the_decoder_to_standard() -> None:
    orch = _orch(_cfg(tx_latency_max_s=1.5))
    orch.decode_source = SimpleNamespace(decoder_mode="extreme", _consecutive_late_slots=0)
    for _ in range(2):
        await _tx(orch, 2.4, in_slot=True)
    assert orch.decode_source.decoder_mode == "extreme"  # noch nicht
    await _tx(orch, 2.4, in_slot=True)
    assert orch.decode_source.decoder_mode == "standard"


@pytest.mark.asyncio
async def test_one_fast_tx_resets_the_late_counter() -> None:
    orch = _orch(_cfg(tx_latency_max_s=1.5))
    orch.decode_source = SimpleNamespace(decoder_mode="extreme", _consecutive_late_slots=0)
    await _tx(orch, 2.4, in_slot=True)
    await _tx(orch, 2.4, in_slot=True)
    await _tx(orch, 0.5, in_slot=True)  # ein schneller dazwischen
    await _tx(orch, 2.4, in_slot=True)
    await _tx(orch, 2.4, in_slot=True)
    assert orch.decode_source.decoder_mode == "extreme"


@pytest.mark.asyncio
async def test_standard_mode_has_nothing_to_fall_back_to() -> None:
    orch = _orch(_cfg(tx_latency_max_s=1.5))
    orch.decode_source = SimpleNamespace(decoder_mode="standard", _consecutive_late_slots=0)
    for _ in range(3):
        await _tx(orch, 2.4, in_slot=True)
    assert orch.decode_source.decoder_mode == "standard"  # unveraendert, kein Crash


@pytest.mark.asyncio
async def test_manual_tx_never_counts_against_the_decoder() -> None:
    """B4 sichtbar, aber ohne Nebenwirkung: ein CQ-Klick bei Sekunde 9
    ist kein Decoder-Problem."""
    orch = _orch(_cfg(tx_latency_max_s=1.5))
    orch.decode_source = SimpleNamespace(decoder_mode="extreme", _consecutive_late_slots=0)
    for _ in range(5):
        await _tx(orch, 9.0, in_slot=False)
    assert orch.decode_source.decoder_mode == "extreme"
    assert orch.status().tx_start_offset_s == pytest.approx(9.0)  # aber gemessen


@pytest.mark.asyncio
async def test_manual_tx_late_in_the_slot_is_dropped() -> None:
    """B4: der Mid-Slot-Burst eines Klicks entfaellt — die State-Machine
    sendet an der naechsten Grenze. Gemessen wird er trotzdem."""
    orch = _orch(_cfg(tx_latency_max_s=1.5))
    orch.playback = SimpleNamespace(play=lambda pcm: None)  # wuerde sonst PTT tasten
    orch.rig.set_ptt = AsyncMock()
    await _tx(orch, 9.0, in_slot=False)
    orch.rig.set_ptt.assert_not_called()
    assert orch.status().tx_start_offset_s == pytest.approx(9.0)


@pytest.mark.asyncio
async def test_manual_tx_early_in_the_slot_still_goes_out() -> None:
    orch = _orch(_cfg(tx_latency_max_s=1.5))
    assert orch._record_tx_start_offset.__func__ is not None
    orch._slot_phase_s = lambda: 0.6  # type: ignore[method-assign]
    orch._in_slot_tick = False
    assert orch._record_tx_start_offset() is True


@pytest.mark.asyncio
async def test_slot_driven_tx_is_never_dropped_even_when_late() -> None:
    """Spaet ist nicht gleich sinnlos: ein slot-getriebener Burst bei 2,4 s
    ist grenzwertig, aber der Fallback kuemmert sich — nicht das Weglassen."""
    orch = _orch(_cfg(tx_latency_max_s=1.5))
    orch.decode_source = SimpleNamespace(decoder_mode="standard", _consecutive_late_slots=0)
    orch._slot_phase_s = lambda: 2.4  # type: ignore[method-assign]
    orch._in_slot_tick = True
    assert orch._record_tx_start_offset() is True


@pytest.mark.asyncio
async def test_slot_tick_sets_and_clears_the_in_slot_flag() -> None:
    from datetime import UTC, datetime

    from ft8_appliance.runtime.slot_clock import SlotTick

    orch = _orch()
    seen: list[bool] = []

    async def spy(tick):
        seen.append(orch._in_slot_tick)
        return []

    orch.decode_source = spy
    now = datetime.now(UTC)
    await orch.process_slot(SlotTick(index=0, posix=now.timestamp(), utc_start=now))
    assert seen == [True]
    assert orch._in_slot_tick is False


def test_slot_phase_follows_the_mode() -> None:
    import time as _time

    orch = _orch(_cfg(mode="FT8"))
    assert 0.0 <= orch._slot_phase_s() < 15.0
    assert orch._slot_phase_s() == pytest.approx(_time.time() % 15.0, abs=0.05)
    orch_ft4 = _orch(_cfg(mode="FT4"))
    assert 0.0 <= orch_ft4._slot_phase_s() < 7.5


# ================================================================== C5 Gain pro Rig


def test_gain_is_loaded_for_the_current_rig_model(tmp_path) -> None:
    orch = _orch(_cfg("ic705"), tmp_path)
    orch._runtime_state_path.write_text(json.dumps({
        "audio_gain": 0.42,                       # alter Schluessel (IC-7300-Wert)
        "audio_gain_by_rig": {"ic7300": 0.42, "ic705": 0.21},
        "tx_power_w": 5,
    }))
    orch._load_runtime_state()
    assert orch._audio_gain == pytest.approx(0.21)


def test_legacy_file_without_per_rig_map_still_loads(tmp_path) -> None:
    orch = _orch(_cfg("ic7300"), tmp_path)
    orch._runtime_state_path.write_text(json.dumps({"audio_gain": 0.33, "tx_power_w": 50}))
    orch._load_runtime_state()
    assert orch._audio_gain == pytest.approx(0.33)


def test_unknown_rig_in_map_falls_back_to_config_default(tmp_path) -> None:
    """Map vorhanden, aber ohne dieses Modell: NICHT den Wert eines anderen
    Rigs nehmen — Config-Default bleibt."""
    orch = _orch(_cfg("ic705"), tmp_path)
    default = orch._audio_gain
    orch._runtime_state_path.write_text(json.dumps({
        "audio_gain": 0.42, "audio_gain_by_rig": {"ic7300": 0.42}, "tx_power_w": 5,
    }))
    orch._load_runtime_state()
    assert orch._audio_gain == pytest.approx(0.42)  # Legacy-Fallback greift ...
    # ... aber nur, weil per-Rig fuer ic705 fehlt. Sobald ein ic705-Eintrag da
    # ist, zaehlt der:
    orch._runtime_state_path.write_text(json.dumps({
        "audio_gain": 0.42, "audio_gain_by_rig": {"ic7300": 0.42, "ic705": 0.2},
    }))
    orch._audio_gain = default
    orch._load_runtime_state()
    assert orch._audio_gain == pytest.approx(0.2)


def test_persist_writes_the_per_rig_map_and_keeps_other_rigs(tmp_path) -> None:
    orch = _orch(_cfg("ic705"), tmp_path)
    orch._runtime_state_path.write_text(json.dumps({
        "audio_gain": 0.42, "audio_gain_by_rig": {"ic7300": 0.42}, "tx_power_w": 5,
    }))
    orch._audio_gain = 0.25
    orch._maybe_persist_runtime_state(force=True)
    data = json.loads(orch._runtime_state_path.read_text())
    assert data["audio_gain_by_rig"] == {"ic7300": 0.42, "ic705": 0.25}
    assert data["audio_gain"] == 0.25  # alter Schluessel bleibt lesbar


@pytest.mark.asyncio
async def test_rig_change_does_not_carry_the_old_rigs_gain(tmp_path) -> None:
    """Der eingeregelte IC-7300-Gain darf den IC-705 nicht uebersteuern."""
    orch = _orch(_cfg("ic7300"), tmp_path)
    orch._last_rig_hamlib_id = orch.config.rig.hamlib_id
    orch._audio_gain = 0.6
    await orch.on_config_changed(_cfg("ic705"))
    assert orch._audio_gain == pytest.approx(float(OperatingConfig().audio_gain))


@pytest.mark.asyncio
async def test_rig_change_uses_the_new_rigs_stored_gain_when_present(tmp_path) -> None:
    orch = _orch(_cfg("ic7300"), tmp_path)
    orch._last_rig_hamlib_id = orch.config.rig.hamlib_id
    orch._runtime_state_path.write_text(json.dumps({
        "audio_gain_by_rig": {"ic7300": 0.6, "ic705": 0.18},
    }))
    orch._audio_gain = 0.6
    await orch.on_config_changed(_cfg("ic705"))
    assert orch._audio_gain == pytest.approx(0.18)
