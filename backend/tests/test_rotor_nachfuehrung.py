"""Rotor-Nachfuehrung (seit 2026-11-15).

Die Richtantenne am MK2 haengt an einem Create RC5B-3 mit Endanschlag im
Sueden. Gedreht wird dorthin, wo Empfaenger im eigenen Feld laut PSK
Reporter die meisten Sender hoeren — nie waehrend einer Aussendung, nie
mitten im QSO, und Handbetrieb am Steuergeraet hat Vorrang.
"""
from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import time
from unittest.mock import AsyncMock

import pytest

from ft8_appliance.analyse import richtung as ri
from ft8_appliance.config import (
    AntennaConfig,
    AppConfig,
    BandConfig,
    OperatingConfig,
    OperatorConfig,
    RotorConfig,
)
from ft8_appliance.integrations.psk_richtung import parse_sender
from ft8_appliance.rig.rigctld_client import RigSnapshot
from ft8_appliance.rig.rotctld_client import RotctldClient
from ft8_appliance.runtime import FakeSlotClock, Orchestrator

# ------------------------------------------------------------------ Rechnung


def test_peilung_nach_nordamerika_und_japan() -> None:
    az, km = ri.peilung("JN58CH", "FN42")       # Boston
    assert 285 < az < 300 and 5800 < km < 6300
    az, km = ri.peilung("JN58CH", "PM95")       # Tokio
    assert 25 < az < 45 and km > 8500


def test_peilung_kaputter_locator() -> None:
    assert ri.peilung("JN58CH", "RR73") is None
    assert ri.peilung("JN58CH", "") is None
    assert ri.peilung("JN58CH", "12AB") is None


def test_zaehlung_ein_sender_einmal_und_nur_aus_der_ferne() -> None:
    sender = [("K1ABC", "FN42"), ("K1ABC", "FN42"), ("K1ABC", "FN42"),
              ("W2XYZ", "FN31"), ("DL1AA", "JN48"), ("JA1ZZ", "PM95")]
    f = ri.zaehle_sender(sender, "JN58CH", min_km=1000)
    assert sum(f.values()) == 3                 # DL1AA zu nah, K1ABC nur einmal
    assert sum(n for az, n in f.items() if 280 <= az <= 310) == 2


def test_beste_richtung_mit_oeffnungswinkel() -> None:
    f = {300: 10, 290: 8, 60: 12}
    az, p = ri.beste_richtung(f, 60)
    assert p == 18 and 270 <= az <= 320         # zwei Faecher zusammen schlagen eines


def test_drehweg_kreuzt_den_endanschlag_nicht() -> None:
    # Anschlag Sued: von 240 nach 120 ueber Norden, nicht die kurzen 120 Grad
    assert ri.drehweg(240, 120, 180) == pytest.approx(240)
    assert ri.drehweg(300, 60, 180) == pytest.approx(120)
    assert ri.drehweg(170, 190, 180) == pytest.approx(340)
    assert ri.drehweg(10, 350, 180) == pytest.approx(20)


@pytest.mark.parametrize("ist,faecher,drehen,grund", [
    (300, {60: 40, 300: 5}, True, "besser"),
    (300, {300: 40, 60: 5}, False, "steht_richtig"),
    (300, {60: 3, 300: 2}, False, "zu_wenig_meldungen"),
    (300, {60: 22, 300: 20}, False, "vorteil_zu_klein"),
    (None, {60: 40}, False, "position_unbekannt"),
])
def test_entscheidung(ist, faecher, drehen, grund) -> None:
    e = ri.entscheide(ist, faecher, oeffnung_grad=60, min_schritt_grad=30,
                      min_sender=20, min_vorteil=0.25)
    assert (e.drehen, e.grund) == (drehen, grund)


# Zwei Zeilen aus der Antwort vom 15.11.2026 (JN58, 15 m), dazu eine ohne Locator
XML = """<?xml version="1.0"?>
<receptionReports >
  <lastSequenceNumber value="74874555887"/>
  <receptionReport receiverCallsign="DO7TTR" receiverLocator="JN58JS" senderCallsign="DF3IS" senderLocator="JN49LL" frequency="21075102" flowStartSeconds="1794739050" mode="FT8" isReceiver="1" sNR="-24" />
  <receptionReport receiverCallsign="DO7TTR" receiverLocator="JN58JS" senderCallsign="JA1ZZ" senderLocator="PM95vq" frequency="21075800" flowStartSeconds="1794739050" mode="FT8" sNR="-12" />
  <receptionReport receiverCallsign="DO7TTR" receiverLocator="JN58JS" senderCallsign="XX9X" frequency="21075800" flowStartSeconds="1794739050" mode="FT8" sNR="-12" />
</receptionReports>
"""


def test_psk_antwort_wird_gelesen() -> None:
    assert parse_sender(XML) == [("DF3IS", "JN49LL"), ("JA1ZZ", "PM95vq")]
    assert parse_sender("<html>Fehler</html>") == []


# ---------------------------------------------------------- rotctld (Dummy)


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(shutil.which("rotctld") is None, reason="hamlib rotctld fehlt")
async def test_client_gegen_dummy_rotor() -> None:
    port = _freier_port()
    proc = subprocess.Popen(["rotctld", "-m", "1", "-T", "127.0.0.1", "-t", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        c = RotctldClient("127.0.0.1", port, timeout=3.0)
        for _ in range(50):
            try:
                await c.position()
                break
            except Exception:
                await asyncio.sleep(0.1)
        await c.drehe_auf(45)
        ende = time.monotonic() + 30
        az = await c.position()
        while abs(az - 45) > 1 and time.monotonic() < ende:
            await asyncio.sleep(0.5)
            az = await c.position()
        assert abs(az - 45) <= 1
        await c.stopp()
        await c.close()
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# ------------------------------------------------------------ Orchestrator


class _Rotor:
    def __init__(self, az: float) -> None:
        self.az = az
        self.befehle: list[str] = []

    async def position(self) -> float:
        return self.az

    async def drehe_auf(self, az: float) -> None:
        self.befehle.append(f"P {az:.0f}")

    async def stopp(self) -> None:
        self.befehle.append("S")


class _Psk:
    def __init__(self, sender) -> None:
        self.sender = sender
        self.abrufe = 0

    async def sender_im_feld(self, loc, band, **_):
        self.abrufe += 1
        return self.sender


# 30 Sender in Japan/Asien, 4 in Nordamerika
ASIEN = [(f"JA{i}AA", "PM95") for i in range(30)] + [(f"K{i}AA", "FN42") for i in range(4)]


def _orch() -> Orchestrator:
    cfg = AppConfig(
        operator=OperatorConfig(callsign="DO3XR", default_locator="JN58ch", license_class="E"),
        bands=[BandConfig(name="15m", freq_khz=21074, antenna="gross")],
        antennas=[AntennaConfig(name="gross", bands=["15m"])],
        operating=OperatingConfig(),
        rotor=RotorConfig(enabled=True, antenne="gross"),
    )
    gps = AsyncMock()
    gps.snapshot = type("Gps", (), {"mode": 3, "lat": None, "lon": None, "ts": None,
                                    "lock_for_min": None, "satellites_used": None})()

    async def _keine(_tick):
        return []

    o = Orchestrator(config=cfg, rig=AsyncMock(), gps=gps, decode_source=_keine,
                     slot_clock=FakeSlotClock(count=0), db_enabled=False)
    o._active_antenna = "gross"
    o._last_rig = RigSnapshot(freq_hz=21_074_000)
    o.state_machine.ctx.auto_answer = True
    o._rotor_kontrolle_haelt = lambda _t: False   # type: ignore[method-assign]
    return o


async def _beschliessen_und_drehen(o, rot, psk) -> None:
    await o._rotor_schritt(rot, psk)               # beschliesst
    await o._rotor_schritt(rot, psk)               # Station frei: dreht


async def test_dreht_nach_asien_und_pausiert_neue_anrufe_bis_zur_ankunft() -> None:
    o, rot, psk = _orch(), _Rotor(300.0), _Psk(ASIEN)
    await o._rotor_schritt(rot, psk)
    assert rot.befehle == [] and o.state_machine.ctx.rotor_dreht is True
    await o._rotor_schritt(rot, psk)
    assert rot.befehle and rot.befehle[0].startswith("P ")
    ziel = float(rot.befehle[0].split()[1])
    assert 20 <= ziel <= 50
    assert o.state_machine.ctx.rotor_dreht is True
    assert o.state_machine._pick_hunt_target([]) is None   # nichts Neues anfangen
    rot.az = ziel                                         # angekommen
    await o._rotor_schritt(rot, psk)
    assert o.state_machine.ctx.rotor_dreht is False
    # Danach bleibt sie stehen, auch wenn die Lage sich dreht
    psk.sender = [(f"K{i}AA", "FN42") for i in range(40)]
    n = len(rot.befehle)
    await o._rotor_schritt(rot, psk)
    assert len(rot.befehle) == n


async def test_kontrollstunde_dreht_nicht() -> None:
    o, rot, psk = _orch(), _Rotor(300.0), _Psk(ASIEN)
    o._rotor_kontrolle_haelt = lambda _t: True   # type: ignore[method-assign]
    await o._rotor_schritt(rot, psk)
    assert rot.befehle == []
    assert o._rotor_status["grund"] == "kontrolle"
    assert o.state_machine.ctx.rotor_dreht is False


async def test_bei_ptt_wird_angehalten_und_danach_weitergedreht() -> None:
    o, rot, psk = _orch(), _Rotor(300.0), _Psk(ASIEN)
    await _beschliessen_und_drehen(o, rot, psk)
    ziel = rot.befehle[0]
    o._tx_burst_active = True
    await o._rotor_schritt(rot, psk)
    assert rot.befehle[-1] == "S"
    await o._rotor_schritt(rot, psk)
    assert rot.befehle[-1] == "S"                         # noch am Senden: kein neuer Befehl
    o._tx_burst_active = False
    await o._rotor_schritt(rot, psk)
    assert rot.befehle[-1] == ziel


async def test_von_hand_gedreht_hat_vorrang() -> None:
    o, rot, psk = _orch(), _Rotor(300.0), _Psk([])
    await o._rotor_schritt(rot, psk)                      # uebernimmt 300 als Soll
    rot.az = 200.0                                        # jemand dreht am Steuergeraet
    psk.sender = ASIEN
    await o._rotor_schritt(rot, psk)
    assert rot.befehle == []
    assert o._rotor_status.get("ruht_bis_min") == o.config.rotor.hand_pause_min
    await o._rotor_schritt(rot, psk)
    assert rot.befehle == []                              # ruht weiter


async def test_haengender_rotor_wird_angehalten() -> None:
    o, rot, psk = _orch(), _Rotor(300.0), _Psk(ASIEN)
    await _beschliessen_und_drehen(o, rot, psk)
    o._rotor_befehl_at -= o.config.rotor.fahrzeit_max_s + 1
    await o._rotor_schritt(rot, psk)
    assert rot.befehle[-1] == "S"
    assert o.state_machine.ctx.rotor_dreht is False


async def test_im_qso_beschlossen_gedreht_erst_danach() -> None:
    """Auf einem vollen Band laeuft fast immer ein QSO. Die Drehung wird
    trotzdem beschlossen; danach faengt die Station nichts Neues an, das
    laufende QSO laeuft zu Ende, dann dreht der Rotor."""
    from ft8_appliance.statemachine.states import State
    o, rot, psk = _orch(), _Rotor(300.0), _Psk(ASIEN)
    o.state_machine.state = State.QSO_REPORT
    await o._rotor_schritt(rot, psk)
    await o._rotor_schritt(rot, psk)
    assert rot.befehle == [] and psk.abrufe == 1
    assert o.state_machine.ctx.rotor_dreht is True          # kein neuer Anruf
    o.state_machine.state = State.IDLE                       # QSO fertig
    await o._rotor_schritt(rot, psk)
    assert rot.befehle and rot.befehle[0].startswith("P ")


async def test_wartet_hoechstens_fuenf_minuten_auf_das_qso_ende() -> None:
    from ft8_appliance.statemachine.states import State
    o, rot, psk = _orch(), _Rotor(300.0), _Psk(ASIEN)
    o.state_machine.state = State.QSO_REPORT
    await o._rotor_schritt(rot, psk)
    ziel, seit, *rest = o._rotor_wartend
    o._rotor_wartend = (ziel, seit - o._ROTOR_WARTEN_MAX_S - 1, *rest)
    await o._rotor_schritt(rot, psk)
    assert rot.befehle == [] and o._rotor_wartend is None
    assert o.state_machine.ctx.rotor_dreht is False


@pytest.mark.parametrize("stoerung", ["cq", "andere_antenne", "kein_hunting"])
async def test_dreht_nur_im_hunting(stoerung) -> None:
    o, rot, psk = _orch(), _Rotor(300.0), _Psk(ASIEN)
    if stoerung == "cq":
        o.state_machine.ctx.auto_cq = True
    elif stoerung == "andere_antenne":
        o._active_antenna = "dipol"
    else:
        o.state_machine.ctx.auto_answer = False
    await o._rotor_schritt(rot, psk)
    assert rot.befehle == [] and psk.abrufe == 0


def test_standardmaessig_aus_und_ohne_status() -> None:
    assert RotorConfig().enabled is False
    o = _orch()
    o.config.rotor.enabled = False
    o._rx_audio_dbfs_peak = None
    o._rx_audio_dbfs_peak_ts = 0.0
    assert o.status().rotor is None


def test_rotctld_unit_nur_lokal_und_nicht_automatisch_eingerichtet() -> None:
    from pathlib import Path
    wurzel = Path(__file__).resolve().parents[2]
    unit = (wurzel / "deploy/systemd/ft8-rotctld.service.in").read_text()
    assert "-T 127.0.0.1" in unit and "User=@SERVICE_USER@" in unit
    # Erst nach dem Einbau des Interfaces von Hand einrichten
    assert "ft8-rotctld" not in (wurzel / "scripts/self-update.sh").read_text()
    assert "ft8-rotctld" not in (wurzel / "deploy/install.sh").read_text()


async def test_trockenlauf_entscheidet_ohne_pause_und_ohne_kontrolle() -> None:
    from ft8_appliance.runtime.orchestrator import _TrockenRotor
    o, psk = _orch(), _Psk(ASIEN)
    o.config.rotor.trockenlauf = True
    o._rotor_kontrolle_haelt = lambda _t: True   # type: ignore[method-assign]  # gilt nicht
    rot = _TrockenRotor()
    await o._rotor_schritt(rot, psk)
    assert 20 <= rot.az <= 50                     # "steht" sofort am Ziel
    assert o.state_machine.ctx.rotor_dreht is False
    assert o._rotor_status["grund"] == "trockenlauf"
    await o._rotor_schritt(rot, psk)              # keine Hand-Erkennung: Position = Soll
    assert "ruht_bis_min" not in o._rotor_status


async def test_verschleissgrenze_je_tag() -> None:
    from datetime import UTC, datetime
    o, rot, psk = _orch(), _Rotor(300.0), _Psk(ASIEN)
    heute = datetime.now(UTC).strftime("%Y-%m-%d")
    o._rotor_weg_tag = (heute, o.config.rotor.max_drehweg_tag_grad - 10)
    await o._rotor_schritt(rot, psk)
    assert rot.befehle == []
    assert o._rotor_status["grund"] == "tageslimit"
    o._rotor_weg_tag = ("2000-01-01", 99999.0)       # neuer Tag: Zaehler beginnt neu
    o._rotor_entschieden_at = -1e9
    await _beschliessen_und_drehen(o, rot, psk)
    assert rot.befehle and rot.befehle[0].startswith("P ")
