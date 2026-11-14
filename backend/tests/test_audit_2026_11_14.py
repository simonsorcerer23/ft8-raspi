"""Befunde des Audits vom 14.11.2026, je Befund ein Test.

Sieben Pruefer haben die Box bereichsweise gelesen; die Befunde stehen in
docs/audit_2026_11_14.md. Hier steht, was davon behoben ist, in der
Reihenfolge der Behebung.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from ft8_appliance.config import AppConfig, set_config_for_tests
from ft8_appliance.integrations import eqsl_inbox
from ft8_appliance.util.config_snapshot import MASKE, maskiere
from ft8_appliance.util.system_health import ChronyStatus, parse_chrony_tracking
from ft8_appliance.web import create_app
from ft8_appliance.web.routes.config import auf_laufende_legen

from .test_web import FakeOrchestrator

# ---------------------------------------------------------------------------
# Zeit-Guard: chronyd ohne Quelle ist nicht synchron

CHRONY_OHNE_QUELLE = """Reference ID    : 00000000 ()
Stratum         : 0
Ref time (UTC)  : Thu Jan 01 00:00:00 1970
System time     : 0.000000000 seconds fast of NTP time
Last offset     : +0.000000000 seconds
RMS offset      : 0.000000000 seconds
Frequency       : 4.112 ppm slow
Residual freq   : +0.000 ppm
Skew            : 0.000 ppm
Root delay      : 1.000000000 seconds
Root dispersion : 1.000000000 seconds
Update interval : 0.0 seconds
Leap status     : Not synchronised
"""


def test_chrony_ohne_quelle_gilt_nicht_als_synchron() -> None:
    """Stratum 0 und Abweichung 0,000 s sahen fuer den Guard gruen aus."""
    s = parse_chrony_tracking(CHRONY_OHNE_QUELLE)
    assert s is not None and s.stratum == 0 and s.offset_s == 0.0
    assert s.synchronisiert is False


@pytest.mark.parametrize(("stratum", "leap", "erwartet"), [
    (2, "Normal", True),
    (1, "Normal", True),
    (15, "Normal", True),
    (16, "Normal", False),
    (0, "Normal", False),
    (None, "Normal", False),
    (2, "Not", False),
    (3, None, True),          # aeltere Fixtures ohne Leap-Zeile
])
def test_chrony_synchronisiert(stratum, leap, erwartet) -> None:
    assert ChronyStatus(offset_s=0.001, stratum=stratum, leap_status=leap).synchronisiert is erwartet


# ---------------------------------------------------------------------------
# API ohne geladenen Token bleibt zu

def test_ohne_token_ist_die_api_von_aussen_zu() -> None:
    set_config_for_tests(AppConfig.model_validate({
        "operators": [{"callsign": "DK9XR", "license_class": "A"}],
        "active_callsign": "DK9XR",
    }))
    app = create_app()
    app.state.orchestrator = FakeOrchestrator()
    c = TestClient(app)                      # Host "testclient", nicht localhost
    assert c.get("/api/status").status_code == 503
    assert c.post("/api/system/self-update").status_code == 503
    assert c.get("/").status_code != 503     # die Oberflaeche selbst bleibt erreichbar


# ---------------------------------------------------------------------------
# Speichern der Konfigseite setzt nichts zurueck, was sie nicht kennt

def _laufend() -> AppConfig:
    return AppConfig.model_validate({
        "operators": [{"callsign": "DO3XR", "license_class": "E", "default_locator": "JN58ch"}],
        "active_callsign": "DO3XR",
        "demo_mode": True,
        "rig": {"model": "ic7300", "max_power_w": 50},
        "operating": {
            "decoder_late_pass": False, "autopilot_psk_aktivitaet": True,
            "rig_tuner_soll": "aus", "autopilot_allowed_bands": ["15m", "10m"],
        },
    })


def test_nicht_genannte_felder_behalten_ihren_wert() -> None:
    cfg = _laufend()
    neu = AppConfig.model_validate(auf_laufende_legen(
        {"operating": {"mode": "FT4", "autopilot_allowed_bands": ["15m"]}}, cfg))
    assert neu.operating.mode == "FT4"
    assert neu.operating.autopilot_allowed_bands == ["15m"]      # Liste gilt als Ganzes
    assert neu.operating.decoder_late_pass is False
    assert neu.operating.autopilot_psk_aktivitaet is True
    assert neu.operating.rig_tuner_soll == "aus"
    assert neu.demo_mode is True
    assert neu.rig.max_power_w == 50


def test_leeres_speichern_aendert_nichts() -> None:
    cfg = _laufend()
    assert AppConfig.model_validate(auf_laufende_legen({}, cfg)) == cfg


def test_ausdrueckliches_null_leert_weiterhin() -> None:
    neu = AppConfig.model_validate(auf_laufende_legen({"rig": {"max_power_w": None}}, _laufend()))
    assert neu.rig.max_power_w is None
    assert neu.rig.model == "ic7300"


# ---------------------------------------------------------------------------
# Geheimnisse: Aenderungshistorie und Journal

def test_logbuch_schluessel_der_rufzeichen_varianten_werden_maskiert() -> None:
    """qrz_logbooks bildet Rufzeichen auf Schluessel ab; der Feldname ist ein Rufzeichen."""
    roh = {"operators": [{
        "callsign": "DK9XR", "qrz_logbooks": {"DK9XR/AM": "AAAA-BBBB-CCCC-DDDD", "DK9XR/MM": ""},
        "lotw_station_locations": {"DK9XR/P": "Weissenhorn"}, "license_class": "A",
    }]}
    op = maskiere(roh)["operators"][0]
    assert op["qrz_logbooks"] == {"DK9XR/AM": MASKE, "DK9XR/MM": ""}
    assert op["lotw_station_locations"] == {"DK9XR/P": "Weissenhorn"}
    assert op["license_class"] == "A"


async def test_eqsl_passwort_steht_nicht_im_fehlertext(monkeypatch) -> None:
    """httpx nennt bei 5xx die ganze Adresse samt Password=…; die ging ins Journal."""
    echt = httpx.AsyncClient

    class Fehler(echt):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(lambda r: httpx.Response(503, text="nein"))
            super().__init__(*a, **kw)

    monkeypatch.setattr(eqsl_inbox.httpx, "AsyncClient", Fehler)
    with pytest.raises(eqsl_inbox.EqslInboxError) as e1:
        await eqsl_inbox.hole_inbox("TESTUSER", "GEHEIM-PROBE-123")
    karte = eqsl_inbox.Karteneintrag("EA1ABC", "20260101", "1200", "20M", "FT8", None, None, None)
    with pytest.raises(eqsl_inbox.EqslInboxError) as e2:
        await eqsl_inbox.hole_karte("TESTUSER", "GEHEIM-PROBE-123", karte)
    for e in (e1, e2):
        assert "GEHEIM-PROBE-123" not in str(e.value)
        assert "503" in str(e.value)


# ---------------------------------------------------------------------------
# Auswahllogik: Band, einsame Rufer, hamqsl-Bedingungen

import datetime as dt  # noqa: E402
import time  # noqa: E402

from ft8_appliance.rig.rigctld_client import RigSnapshot  # noqa: E402
from ft8_appliance.runtime import SlotTick  # noqa: E402
from ft8_appliance.statemachine.machine import (  # noqa: E402
    _tier_new_dxcc_band,
    _tier_new_grid_band,
)
from ft8_appliance.statemachine.states import DecodedMsg  # noqa: E402

from .test_autopilot_psk import _orch  # noqa: E402


def _cq(call: str, grid: str = "IN80") -> DecodedMsg:
    return DecodedMsg(ts=dt.datetime.now(dt.UTC), call_from=call, call_to=None, grid=grid,
                      message=f"CQ {call} {grid}", snr_db=-5, dt_s=0.1,
                      freq_offset_hz=1500, band="15m")


def _tick(i: int) -> SlotTick:
    posix = 1_800_000_000 + 15 * i
    return SlotTick(index=i, posix=posix, utc_start=dt.datetime.fromtimestamp(posix, tz=dt.UTC))


async def test_picker_rechnet_mit_dem_band_des_rigs() -> None:
    """ctx.band blieb auf jedem Band "20m": Auf 15 m galt jede Station als neu."""
    o = _orch()
    o._last_rig = RigSnapshot(freq_hz=21_074_000)
    o._last_rig_at = time.monotonic()
    o._rx_audio_dbfs_peak = None                        # setzt sonst start()
    assert o.state_machine.ctx.band == "20m"            # der Vorgabewert
    await o.process_slot(_tick(0))
    ctx = o.state_machine.ctx
    assert ctx.band == "15m"
    ctx.call_to_dxcc = {"EA4GA": "Spain"}
    ctx.worked_dxcc_band = {("Spain", "15m")}
    ctx.worked_grid_band = {("IN80", "15m")}
    assert _tier_new_dxcc_band(_cq("EA4GA"), ctx) == 0
    assert _tier_new_grid_band(_cq("EA4GA"), ctx) == 0


async def test_einsamer_rufer_steht_im_slot_in_dem_er_ruft() -> None:
    """lonely_cq wurde vor dem Decode gerechnet und traf immer die Gegenparitaet."""
    o = _orch()
    gerade = [_cq("DL1ABC")]
    ungerade = [_cq("DL2XYZ", "JO31")]
    for i in range(6):
        decodes = gerade if i % 2 == 0 else ungerade
        o._last_decodes = (ungerade if i % 2 == 0 else gerade)     # der Slot davor
        await o._refresh_decode_context(decodes)
        if i >= 2:
            assert o.state_machine.ctx.lonely_cq_calls == {decodes[0].call_from}, i
        o._slot_history.append(list(decodes))
        del o._slot_history[:-4]


def test_hamqsl_bedingungen_erreichen_die_bandwahl() -> None:
    """Gesucht wurde mit "15m" in einer Tabelle, die "17m-15m" kennt."""
    o = _orch()
    ohne = o._autopilot_propagation_prior("15m")
    o._band_conditions_day = {"17m-15m": "Poor", "12m-10m": "Good"}
    o._band_conditions_night = dict(o._band_conditions_day)
    assert o._autopilot_propagation_prior("15m") == ohne - 8.0
    assert o._autopilot_propagation_prior("10m") == ohne + 10.0


# ---------------------------------------------------------------------------
# eQSL-Posteingang: nur der aktive Operator

def test_posteingang_holt_nur_der_aktive_operator() -> None:
    """Beide Stationen haben beide Operatoren in der Konfiguration; jede holte beide."""
    from ft8_appliance.runtime import FakeSlotClock, Orchestrator
    from unittest.mock import AsyncMock

    cfg = AppConfig.model_validate({
        "operators": [
            {"callsign": "DK9XR", "license_class": "A", "eqsl_user": "DK9XR", "eqsl_password": "a"},
            {"callsign": "DO3XR", "license_class": "E", "eqsl_user": "DO3XR", "eqsl_password": "b"},
        ],
        "active_callsign": "DO3XR",
    })

    async def _keine(_t):
        return []
    gps = AsyncMock()
    o = Orchestrator(config=cfg, rig=AsyncMock(), gps=gps, decode_source=_keine,
                     slot_clock=FakeSlotClock(count=0), db_enabled=False)
    assert [op.callsign for op in o._qsl_operatoren()] == ["DO3XR"]
    cfg.operators[1].eqsl_password = None
    assert o._qsl_operatoren() == []
