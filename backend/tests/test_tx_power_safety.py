"""TX-Power Safety-Floor Tests (Sebastian 2026-05-24, v0.2.3).

Variante B: clamp-down-only Safety-Floor. Bei Reset-Events (Boot,
Operator-Wechsel, Rig-Wechsel, Bandwechsel) wird ``_tx_power_w`` auf
``max(1, effective_max // 2)`` clamped — **nur wenn aktuell drueber**.
QRP-Settings darunter bleiben unberuehrt.

Diese Tests gehen direkt an die Safety-Floor-Helper ohne MockRigctld
zu starten — fuer Unit-Speed. Wir injizieren AsyncMock-Rig.
"""

from __future__ import annotations

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


def _cfg(
    *,
    license_class: str = "E",
    rig_model: str = "ic7300",
    rig_max_override: int | None = None,
    default_power_w: int = 100,
) -> AppConfig:
    """Standard-Config: E-Klasse, IC-7300 (100W rig-max)."""
    rig = RigConfig(model=rig_model)
    if rig_max_override is not None:
        rig = RigConfig(model=rig_model, max_power_w=rig_max_override)
    return AppConfig(
        operator=OperatorConfig(
            callsign="DK9XR",
            default_locator="JN58td",
            default_power_w=default_power_w,
            license_class=license_class,
        ),
        rig=rig,
        bands=[
            BandConfig(name="15m", freq_khz=21074, antenna="endfed"),
            BandConfig(name="10m", freq_khz=28074, antenna="endfed"),
        ],
        antennas=[AntennaConfig(name="endfed", bands=["15m", "10m"])],
        operating=OperatingConfig(),
    )


def _build_orch(cfg: AppConfig) -> Orchestrator:
    """Minimal-Orchestrator mit gemockter Rig + GPS, ohne start()."""
    rig = AsyncMock()
    rig.set_rfpower = AsyncMock(return_value=None)
    rig.snapshot = AsyncMock(return_value=RigSnapshot(freq_hz=14_074_000))
    rig.close = AsyncMock(return_value=None)
    gps = AsyncMock()
    gps.snapshot = type("S", (), {"mode": 3, "lat": 0, "lon": 0, "ts": None,
                                  "lock_for_min": None, "satellites_used": None})()
    gps.close = AsyncMock(return_value=None)

    async def _no_decodes(tick):
        return []

    return Orchestrator(
        config=cfg,
        rig=rig,
        gps=gps,
        decode_source=_no_decodes,
        slot_clock=FakeSlotClock(count=0),
    )


# ---------------------------------------------------------------- compute


def test_compute_safe_default_uses_band_cap_when_available() -> None:
    """20m E-Klasse: license=100W, rig=100W → safe=50W."""
    orch = _build_orch(_cfg())
    safe = orch._compute_safe_default_power_w("15m")
    assert safe == 50


def test_compute_safe_default_falls_back_to_rig_max_for_unknown_band() -> None:
    """band=None → fall back auf rig.effective_max_power_w / 2."""
    orch = _build_orch(_cfg())
    safe = orch._compute_safe_default_power_w(None)
    assert safe == 50  # IC-7300 = 100W rig-max → 50W


def test_compute_safe_default_min_one_watt() -> None:
    """Edge: effective_max=1 → safe=1 (kein 0)."""
    orch = _build_orch(_cfg(rig_max_override=1))
    safe = orch._compute_safe_default_power_w("15m")
    assert safe == 1


def test_compute_safe_default_qmx_plus_5w() -> None:
    """QMX+ 5W → safe=2W (5//2=2)."""
    orch = _build_orch(_cfg(rig_model="qmx_plus"))
    safe = orch._compute_safe_default_power_w("15m")
    assert safe == 2


def test_compute_safe_default_returns_none_for_disallowed_band() -> None:
    """Sebastian-Bug v0.4.5: E-Klasse darf nicht auf 20m. Statt
    safe=1W zu liefern (was den User-Slider boesartig auf 1W kickt
    selbst beim Zurueckwechsel auf erlaubtes Band) -> None ->
    Caller skipt Floor-Application komplett.
    """
    cfg = _cfg(license_class="E")
    # Hack: 20m als verbotenes Band manuell hinzufuegen (E-Klasse darf
    # nicht auf 20m). _cfg() hat 15m+10m, beide erlaubt. Wir koennten
    # alternativ direkt effective_max_power_w mocken, aber so ist's
    # naeher am Real-Bug.
    from ft8_appliance.config import BandConfig
    cfg.bands.append(BandConfig(name="20m", freq_khz=14074, antenna="endfed"))
    orch = _build_orch(cfg)
    safe = orch._compute_safe_default_power_w("20m")
    assert safe is None, (
        "E-Klasse auf 20m → effective_max=0 → safe muss None sein "
        "damit der safety-floor skipt statt auf 1W zu clampen"
    )


@pytest.mark.asyncio
async def test_apply_safety_floor_skips_for_disallowed_band() -> None:
    """Konsequenz aus dem None-Return: _tx_power_w bleibt unveraendert
    wenn das Band nicht freigegeben ist."""
    cfg = _cfg(license_class="E")
    from ft8_appliance.config import BandConfig
    cfg.bands.append(BandConfig(name="20m", freq_khz=14074, antenna="endfed"))
    orch = _build_orch(cfg)
    orch._tx_power_w = 50  # User-Stand
    await orch._apply_tx_power_safety_floor("band_change", band="20m")
    assert orch._tx_power_w == 50, (
        "Nicht auf 1W kicken — User-Slider bleibt, TX wird durch "
        "license/antenna guard separat blockiert"
    )
    orch.rig.set_rfpower.assert_not_awaited()


# ---------------------------------------------------------------- apply()


@pytest.mark.asyncio
async def test_apply_safety_floor_clamps_down_when_above() -> None:
    """Aktuell 80W, safe=50W → clamp auf 50W + rig.set_rfpower(0.5)."""
    orch = _build_orch(_cfg())
    orch._tx_power_w = 80
    orch._last_active_band = "15m"
    await orch._apply_tx_power_safety_floor("boot")
    assert orch._tx_power_w == 50
    orch.rig.set_rfpower.assert_awaited_once_with(0.5)


@pytest.mark.asyncio
async def test_apply_safety_floor_leaves_qrp_alone() -> None:
    """Variante B: aktuell 5W, safe=50W → NICHT hochziehen, lassen."""
    orch = _build_orch(_cfg())
    orch._tx_power_w = 5
    orch._last_active_band = "15m"
    await orch._apply_tx_power_safety_floor("boot")
    assert orch._tx_power_w == 5, "QRP-Setting darf nicht hochgezogen werden"
    orch.rig.set_rfpower.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_safety_floor_exact_safe_is_noop() -> None:
    """Aktuell genau safe → kein set_rfpower-Call."""
    orch = _build_orch(_cfg())
    orch._tx_power_w = 50
    orch._last_active_band = "15m"
    await orch._apply_tx_power_safety_floor("boot")
    assert orch._tx_power_w == 50
    orch.rig.set_rfpower.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_safety_floor_rig_failure_still_sets_internal() -> None:
    """Wenn rig.set_rfpower wirft (z.B. rigctld noch nicht up), wird
    der interne _tx_power_w trotzdem gesetzt — Sync beim naechsten
    erfolgreichen Set."""
    orch = _build_orch(_cfg())
    orch.rig.set_rfpower.side_effect = RuntimeError("rigctld not connected")
    orch._tx_power_w = 100
    orch._last_active_band = "15m"
    await orch._apply_tx_power_safety_floor("boot")
    assert orch._tx_power_w == 50


@pytest.mark.asyncio
async def test_apply_safety_floor_band_arg_overrides_last() -> None:
    """Explizites band-Argument schlaegt _last_active_band."""
    orch = _build_orch(_cfg())
    orch._tx_power_w = 100
    orch._last_active_band = "15m"
    await orch._apply_tx_power_safety_floor("band_change", band="10m")
    assert orch._tx_power_w == 50


# ---------------------------------------------------------------- rig change


@pytest.mark.asyncio
async def test_rig_change_in_on_config_changed_triggers_safety_floor() -> None:
    """Wenn rig.hamlib_id sich aendert (z.B. IC-7300 → QMX+), safety-
    floor feuert und drueckt 100W auf max(2, irgendwas) runter."""
    orch = _build_orch(_cfg())  # IC-7300, hamlib 3073
    orch._tx_power_w = 100
    orch._last_rig_hamlib_id = orch.config.rig.hamlib_id  # 3073

    new_cfg = _cfg(rig_model="qmx_plus")  # hamlib 2053, 5W max → safe=2W
    await orch.on_config_changed(new_cfg)
    # QMX+ effective_max ist 5W (clamp via on_config_changed laeuft mit
    # last_active_band=None → fallback rig.max=5 → safe=2)
    assert orch._tx_power_w <= 2, \
        f"Rig-Change muss tx_power runter clampen, got {orch._tx_power_w}W"
    assert orch._last_rig_hamlib_id == 2053, \
        "hamlib_id tracking aktualisiert"


@pytest.mark.asyncio
async def test_same_rig_in_on_config_changed_no_clamp() -> None:
    """Wenn sich der Rig NICHT aendert (gleiche hamlib_id), kein
    Safety-Floor-Trigger — auch wenn andere Configfields geaendert wurden."""
    orch = _build_orch(_cfg())
    orch._tx_power_w = 80  # ueber 50W safe
    orch._last_rig_hamlib_id = orch.config.rig.hamlib_id

    new_cfg = _cfg()  # gleicher Rig
    # Nur ein anderes Feld aendern (z.B. callsign)
    new_cfg.operator.callsign = "DO3XR"
    await orch.on_config_changed(new_cfg)
    # tx_power_w bleibt 80 (Rig nicht gewechselt → kein Safety-Floor)
    # (kann durch default_power_w-Mirror geaendert sein → checken NUR
    # ob nicht clamp auf 50W passierte)
    assert orch._tx_power_w != 50 or orch.config.operator.default_power_w == 50, \
        "Ohne Rig-Wechsel kein Safety-Floor"
