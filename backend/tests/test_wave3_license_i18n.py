"""Welle 3 des Fix-Plans zum Tiefenaudit 2026-09-06.

* C1  160 m fuer Klasse E mit segmentabhaengigem Cap; Klasse N NICHT auf
      160 m (beides gegen AFuV Anlage 1, Fassung 24.06.2024, Zeilen 3-5).
* C4  CEPT-Sperrgruende ueber den i18n-Katalog.
* C6  Lesen-Aendern-Schreiben der Config unter einem Lock.
"""

from __future__ import annotations

import asyncio

import pytest

from ft8_appliance.config import (
    AntennaConfig,
    AppConfig,
    BandConfig,
    OperatingConfig,
    OperatorConfig,
)
from ft8_appliance.config.license import LICENSE_BANDS, is_band_allowed, max_power_for
from ft8_appliance.config.models import RigConfig
from ft8_appliance.integrations.cept import cept_compliance

# ================================================================== C1


def test_klasse_e_darf_160m() -> None:
    assert is_band_allowed("E", "160m")


def test_klasse_n_darf_nicht_160m() -> None:
    """Anlage 1, Zeilen 3-5: Klasse N steht auf allen drei 160-m-Segmenten
    mit "–". Stand bis 2026-09-06 faelschlich in LICENSE_BANDS."""
    assert not is_band_allowed("N", "160m")
    assert "160m" not in LICENSE_BANDS["N"]


@pytest.mark.parametrize("cls,khz,expected", [
    ("E", 1840, 100),   # FT8-Dial, erstes Segment
    ("A", 1840, 750),
    ("E", 1860, 75),    # zweites Segment: beide 75 W
    ("A", 1860, 75),
    ("E", 1900, 10),    # drittes Segment: beide 10 W
    ("A", 1950, 10),
])
def test_160m_cap_haengt_am_segment(cls, khz, expected) -> None:
    assert max_power_for(cls, "160m", khz) == expected


def test_160m_ohne_frequenz_nimmt_klassen_default() -> None:
    assert max_power_for("E", "160m") == 100
    assert max_power_for("A", "160m") == 750


def test_segmentlogik_beruehrt_andere_baender_nicht() -> None:
    assert max_power_for("A", "20m", 14074) == 750
    assert max_power_for("A", "60m", 5357) == 15
    assert max_power_for("E", "15m", 21074) == 100


def _cfg(cls: str, band: str, khz: int) -> AppConfig:
    return AppConfig(
        operator=OperatorConfig(callsign="DO3XR", default_locator="JN58td", license_class=cls),
        bands=[BandConfig(name=band, freq_khz=khz, antenna="wire")],
        antennas=[AntennaConfig(name="wire", bands=[band])],
        operating=OperatingConfig(),
        rig=RigConfig(model="ic7300"),
    )


def test_appconfig_reicht_den_dial_an_die_lizenztabelle_durch() -> None:
    assert _cfg("E", "160m", 1840).effective_max_power_w("160m") == 100
    assert _cfg("E", "160m", 1900).effective_max_power_w("160m") == 10
    # Rig-Cap (100 W) bleibt das Minimum fuer Klasse A im ersten Segment
    assert _cfg("A", "160m", 1840).effective_max_power_w("160m") == 100


# ================================================================== C4


def test_cept_gruende_kommen_aus_dem_katalog() -> None:
    from ft8_appliance import i18n as _i18n

    _allowed, reason = cept_compliance("F", "DL", "E")  # Frankreich: nur A
    assert _allowed is False
    assert reason == _i18n.translate("cept.class_e_blocked", None, name="Frankreich")


def test_cept_gruende_sind_zweisprachig() -> None:
    from ft8_appliance import i18n as _i18n

    for key in ("cept.unknown_country", "cept.suspended", "cept.class_a_needs_guest",
                "cept.class_e_blocked", "cept.class_not_recognised"):
        de = _i18n.translate(key, "de", code="XX", name="X", cls="N")
        en = _i18n.translate(key, "en", code="XX", name="X", cls="N")
        assert de and en and de != en, key
        assert "{" not in de and "{" not in en, key  # kein Platzhalter-Leck


def test_cept_unbekanntes_land_bleibt_gesperrt() -> None:
    allowed, reason = cept_compliance("ZZ", "DL", "A")
    assert allowed is False and "ZZ" in (reason or "")


# ================================================================== C6


@pytest.mark.asyncio
async def test_persist_config_serialisiert_sich_ueber_das_lock(tmp_path, monkeypatch) -> None:
    """Zwei gleichzeitige persist_config-Aufrufe duerfen sich nicht
    ueberlappen: der zweite wartet, bis der erste geschrieben hat."""
    from unittest.mock import AsyncMock

    from ft8_appliance.config import loader as loader_mod
    from ft8_appliance.rig.rigctld_client import RigSnapshot
    from ft8_appliance.runtime import FakeSlotClock, Orchestrator

    rig = AsyncMock()
    rig.snapshot = AsyncMock(return_value=RigSnapshot())
    rig.close = AsyncMock()
    gps = AsyncMock()
    gps.close = AsyncMock()

    async def _nd(tick):
        return []

    orch = Orchestrator(config=_cfg("A", "20m", 14074), rig=rig, gps=gps,
                        decode_source=_nd, slot_clock=FakeSlotClock(count=0))
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(loader_mod, "_current_path", path, raising=False)
    monkeypatch.setattr(loader_mod, "get_current_path", lambda: path)

    order: list[str] = []
    real = orch._persist_config_locked

    async def slow_locked() -> None:
        order.append("start")
        await asyncio.sleep(0.05)
        await real()
        order.append("end")

    orch._persist_config_locked = slow_locked  # type: ignore[method-assign]
    await asyncio.gather(orch.persist_config(), orch.persist_config())
    assert order == ["start", "end", "start", "end"]  # nie verschachtelt
    assert path.exists()


def test_alle_drei_schreiber_nutzen_das_lock() -> None:
    """Quelltext-Gate: wer den Config-Zyklus anfasst, laeuft unter dem Lock."""
    import inspect

    from ft8_appliance.runtime.orchestrator import Orchestrator
    from ft8_appliance.web.routes import config as cfg_route
    from ft8_appliance.web.routes import network as net_route

    assert "_config_rmw_lock" in inspect.getsource(Orchestrator.persist_config)
    assert "_config_rmw_lock" in inspect.getsource(cfg_route.save_config)
    assert "_config_rmw_lock" in inspect.getsource(net_route.set_ap_fallback)
