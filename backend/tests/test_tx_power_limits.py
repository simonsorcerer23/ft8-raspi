"""Audit 2026-07-30: der TX-Power-Slider clampte nur gegen den Rig-Cap.

``AppConfig.effective_max_power_w(band)`` ist das MIN aus Lizenz-Cap,
Rig-Cap und CEPT-Power-Cap im Gastland — und existierte samt Tests schon
lange. ``handle_tx_power`` benutzte es aber nicht, sondern
``rig.effective_max_power_w``. Am IC-7300 mit 100 W faellt das im
Heimatbetrieb nicht auf, weil Klasse A/E dort ohnehin >= 100 W duerfen.
Auffaellig wird es genau da, wo es weh tut: 60m (Klasse A: 15 W) und im
CEPT-Ausland mit niedrigerem nationalem Cap.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ft8_appliance.config.models import (
    AntennaConfig, AppConfig, BandConfig, OperatingConfig, OperatorConfig, RigConfig,
)
from ft8_appliance.runtime.orchestrator import Orchestrator


def _config(license_class: str = "A",
            current_op_country: str | None = None) -> AppConfig:
    return AppConfig(
        operators=[OperatorConfig(
            callsign="DK9XR",
            default_locator="JN58",
            license_class=license_class,
            home_country="DL",
            current_operating_country=current_op_country,
        )],
        bands=[
            BandConfig(name="60m", freq_khz=5357),
            BandConfig(name="20m", freq_khz=14074),
        ],
        antennas=[AntennaConfig(name="EFHW", bands=["60m", "20m"])],
        rig=RigConfig(model="ic7300", max_power_w=100),
        operating=OperatingConfig(),
    )


def _stub(cfg: AppConfig, band: str | None) -> SimpleNamespace:
    stub = SimpleNamespace(
        config=cfg,
        _last_active_band=band,
        _tx_power_w=100,
        _register_app_command=lambda *a, **kw: None,
        _maybe_persist_runtime_state=lambda **kw: None,
        commanded_norm=[],
    )
    stub.rig = SimpleNamespace(
        set_rfpower=lambda norm: _append(stub.commanded_norm, norm)
    )
    stub._legal_max_power_w = lambda: Orchestrator._legal_max_power_w(stub)
    return stub


async def _append(sink: list, norm: float) -> None:
    sink.append(norm)


# --------------------------------------------------------------- Clamp


@pytest.mark.asyncio
async def test_slider_is_clamped_to_license_cap_on_60m() -> None:
    """Klasse A darf auf 60m nur 15 W — der Rig-Cap von 100 W ist hier
    irrelevant."""
    stub = _stub(_config(license_class="A"), band="60m")
    await Orchestrator.handle_tx_power(stub, 100)
    assert stub._tx_power_w == 15


@pytest.mark.asyncio
async def test_slider_still_reaches_rig_cap_on_unrestricted_band() -> None:
    stub = _stub(_config(license_class="A"), band="20m")
    await Orchestrator.handle_tx_power(stub, 100)
    assert stub._tx_power_w == 100


@pytest.mark.asyncio
async def test_below_the_cap_is_untouched() -> None:
    stub = _stub(_config(license_class="A"), band="60m")
    await Orchestrator.handle_tx_power(stub, 5)
    assert stub._tx_power_w == 5


@pytest.mark.asyncio
async def test_zero_and_negative_are_floored_to_one_watt() -> None:
    stub = _stub(_config(license_class="A"), band="20m")
    await Orchestrator.handle_tx_power(stub, 0)
    assert stub._tx_power_w == 1


# ------------------------------------------------ Normierung gegen die Rig-Skala


@pytest.mark.asyncio
async def test_rfpower_is_normalised_against_the_rig_scale_not_the_legal_cap() -> None:
    """Der gefaehrliche Halbfix: gegen das legale Limit zu normieren
    wuerde bei 15 W Cap RFPOWER=1.0 kommandieren — also volle 100 W statt
    der erlaubten 15."""
    stub = _stub(_config(license_class="A"), band="60m")
    await Orchestrator.handle_tx_power(stub, 100)
    assert stub.commanded_norm == [pytest.approx(0.15)]


# ------------------------------------------------------------ Fallbacks


def test_unknown_band_falls_back_to_rig_cap() -> None:
    """Beim Boot, vor dem ersten Rig-Poll, ist das Band unbekannt — mehr
    Information haben wir dann nicht."""
    stub = _stub(_config(), band=None)
    assert Orchestrator._legal_max_power_w(stub) == 100


def test_band_not_in_config_falls_back_to_rig_cap() -> None:
    stub = _stub(_config(), band="6m")
    assert Orchestrator._legal_max_power_w(stub) == 100


def test_band_forbidden_for_class_does_not_collapse_slider_to_one_watt() -> None:
    """Klasse E darf 20m gar nicht (Cap 0). Den Slider deswegen auf 1 W zu
    kicken waere ein vergiftetes Geschenk — der license_guard blockt das
    Senden ohnehin, und nach dem Bandwechsel klebte die Leistung sonst
    unten fest (derselbe Bug wie in v0.4.5)."""
    cfg = _config(license_class="E")
    assert cfg.effective_max_power_w("20m") == 0
    stub = _stub(cfg, band="20m")
    assert Orchestrator._legal_max_power_w(stub) == 100
