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


# ---------------------------------------------------------------------------
# QRZ-Historie ueberlebt den Neustart

def _orch_mit_pfad(tmp_path):
    from unittest.mock import AsyncMock

    from ft8_appliance.runtime import FakeSlotClock, Orchestrator

    cfg = AppConfig.model_validate({
        "operators": [{"callsign": "DK9XR", "license_class": "A"},
                      {"callsign": "DO3XR", "license_class": "E"}],
        "active_callsign": "DK9XR",
    })

    async def _keine(_t):
        return []
    o = Orchestrator(config=cfg, rig=AsyncMock(), gps=AsyncMock(), decode_source=_keine,
                     slot_clock=FakeSlotClock(count=0), db_enabled=True)
    o._runtime_state_path = tmp_path / "runtime_state.json"
    return o


def test_qrz_historie_ueberlebt_den_neustart(tmp_path) -> None:
    """Nach jedem Self-Update fehlte bis zu 24 h alles, was nicht in der eigenen DB steht:
    am 14.11. standen 1614 statt 7349 gearbeitete Rufzeichen im Status."""
    alt = _orch_mit_pfad(tmp_path)
    eintraege = [("EA4GA", "20m", "IN80"), ("W1AW", "15m", "FN31"), ("W1AW", "20m", "FN31")]
    alt._qrz_historie_anwenden(eintraege)
    alt._qrz_historie_speichern(eintraege)

    neu = _orch_mit_pfad(tmp_path)                     # der Prozess nach dem Neustart
    assert neu._worked_calls == set()
    neu._qrz_historie_laden()
    assert neu._worked_calls == {"EA4GA", "W1AW"}
    assert ("FN31", "15m") in neu._worked_grid_band and "IN80" in neu._worked_grids


def test_qrz_historie_liegt_je_operator(tmp_path) -> None:
    o = _orch_mit_pfad(tmp_path)
    o._qrz_historie_speichern([("EA4GA", "20m", "IN80")])
    o.config.active_callsign = "DO3XR"
    assert o._qrz_historie_pfad().name == "qrz_historie_DO3XR.json"
    o._qrz_historie_laden()                             # fuer DO3XR liegt nichts
    assert o._worked_calls == set()


def test_kaputte_qrz_historie_haelt_den_start_nicht_auf(tmp_path) -> None:
    o = _orch_mit_pfad(tmp_path)
    o._qrz_historie_pfad().write_text("{kein json")
    o._qrz_historie_laden()
    assert o._worked_calls == set()


# ---------------------------------------------------------------------------
# rigctld-Client: Fehlerantwort ist nicht "aus", abgerissene Verbindung nicht "gesetzt"

def _client_mit_antworten(antworten: list[bytes]):
    from ft8_appliance.rig.rigctld_client import RigctldClient

    class _Leser:
        async def readline(self):
            return antworten.pop(0) if antworten else b""

    class _Schreiber:
        def write(self, _b): ...
        async def drain(self): ...
        def is_closing(self): return False
        def close(self): ...
        async def wait_closed(self): ...

    c = RigctldClient()
    c._reader, c._writer = _Leser(), _Schreiber()
    return c


async def test_fehlerantwort_auf_ptt_abfrage_ist_nicht_ptt_aus() -> None:
    """ "RPRT -11" las der Client als False, und der PTT-Stuck-Waechter stellte seinen Timer zurueck."""
    c = _client_mit_antworten([b"RPRT -11\n", b"1\n", b"0\n"])
    with pytest.raises(RuntimeError):
        await c.get_ptt()
    assert await c.get_ptt() is True
    assert await c.get_func("TUNER") is False


async def test_abgerissene_verbindung_gilt_nicht_als_gesetzt() -> None:
    """Eine leere Antwort (rigctld weg) werteten die Setter als Erfolg."""
    c = _client_mit_antworten([])
    with pytest.raises(ConnectionError):
        await c.set_func("NR", False)
    assert c._writer is None                    # Socket verworfen, der naechste Befehl verbindet neu


async def test_vorverstaerker_und_abschwaecher_kommen_als_level() -> None:
    """Am IC-7300MK2 nachgesehen: "l PREAMP" liefert 1, "u PREAMP" liefert RPRT -1."""
    from ft8_appliance.rig.rigctld_client import RigctldClient

    c = RigctldClient()
    gefragt: list[str] = []

    async def _send(line: str) -> str:
        gefragt.append(line)
        return {"l PREAMP": "1", "l ATT": "0"}.get(line, "RPRT -1")
    c._send = _send
    snap = await c.snapshot()
    assert snap.preamp_on is True and snap.att_on is False
    assert "u PREAMP" not in gefragt and "u ATT" not in gefragt


# ---------------------------------------------------------------------------
# Betriebsskripte

import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

WURZEL = Path(__file__).resolve().parents[2]


def test_feste_unit_dateien_entsprechen_den_vorlagen(tmp_path) -> None:
    """Neben jeder Vorlage (*.in) liegt eine fest ausgeschriebene Fassung fuer
    Installationen ohne install.env; das Self-Update nimmt sie im Rueckfall. Am
    14.11.2026 waren alle fuenf veraltet, der Controller-Unit fehlten Benutzer,
    Reihenfolge und Zeitabgleich."""
    env = tmp_path / "env"
    env.write_text("APP_USER=sebastian\nAPP_GROUP=sebastian\nAPP_HOME=/home/sebastian\n"
                   "APP_DIR=/home/sebastian/ft8-appliance\nSERVICE_USER=sebastian\n"
                   "SERVICE_GROUP=sebastian\n")
    aus = tmp_path / "aus"
    subprocess.run([str(WURZEL / "deploy" / "render-install-files.sh"), str(aus), str(env)],
                   check=True)
    for name in ("ft8-controller.service", "ft8-rigctld.service", "ft8-ap-fallback.service",
                 "ft8-self-update.service", "ft8-self-update.timer"):
        assert (aus / "systemd" / name).read_text() == \
            (WURZEL / "deploy" / "systemd" / name).read_text(), name
    assert (aus / "sudoers.d" / "ft8-self-update").read_text() == \
        (WURZEL / "deploy" / "sudoers.d" / "ft8-self-update").read_text()


# ---------------------------------------------------------------------------
# eQSL-Posteingang: dieselbe Karte fuer beide Operatoren, Netzfehler beim Bild

async def test_gleiche_karte_fuer_beide_operatoren_kippt_den_abgleich_nicht(tmp_path, monkeypatch):
    """Der Schluessel kennt den Operator nicht: Der zweite Abgleich warf einen
    IntegrityError, und die ganze Charge fiel zurueck, alle 30 Minuten wieder."""
    from types import SimpleNamespace

    from sqlalchemy import select

    from ft8_appliance.db import models as m
    from ft8_appliance.db.session import init_engine, session_scope
    from ft8_appliance.integrations.eqsl_inbox import Karteneintrag
    from ft8_appliance.runtime.orchestrator import Orchestrator

    eng = init_engine(tmp_path / "q.sqlite")
    async with eng.begin() as c:
        await c.run_sync(m.Base.metadata.create_all)
    gleich = Karteneintrag("3D2XX", "20261113", "1200", "15M", "FT4", "20261113", None, None)
    eigen = Karteneintrag("EA1ABC", "20261113", "1300", "15M", "FT8", "20261113", None, None)

    async def fake_inbox(user, pw, *, seit=None, qth_nickname=None, timeout=0):
        return [gleich] if user == "dk" else [eigen, gleich]
    monkeypatch.setattr(eqsl_inbox, "hole_inbox", fake_inbox)
    stub = SimpleNamespace(_alert_qsl_neu=lambda c, n: None)
    dk = SimpleNamespace(callsign="DK9XR", eqsl_user="dk", eqsl_password="x", eqsl_qth_nickname=None)
    do = SimpleNamespace(callsign="DO3XR", eqsl_user="do", eqsl_password="x", eqsl_qth_nickname=None)
    await Orchestrator._qsl_liste_abgleichen(stub, dk)
    await Orchestrator._qsl_liste_abgleichen(stub, do)
    await Orchestrator._qsl_liste_abgleichen(stub, do)          # zweiter Lauf: nichts doppelt
    async with session_scope() as s:
        rows = sorted((k.user_callsign, k.call) for k in (await s.execute(select(m.QslKarte))).scalars())
    assert rows == [("DK9XR", "3D2XX"), ("DO3XR", "3D2XX"), ("DO3XR", "EA1ABC")]


def test_netzfehler_beim_posteingang_ist_als_solcher_markiert() -> None:
    assert eqsl_inbox.EqslInboxError("Netzfehler: weg", netz=True).netz is True
    assert eqsl_inbox.EqslInboxError("kein Bild", hart=True).netz is False


# ---------------------------------------------------------------------------
# Decoder-Deckel und Audiopuffer

def test_decoder_gibt_mehr_als_50_decodes_je_slot_zurueck() -> None:
    """Bei 60 Signalen kamen genau 50 zurueck, die schwaechsten fehlten wortlos."""
    from ft8_appliance.decode import ft8_native

    assert ft8_native.MAX_DECODES_PER_SLOT >= 150
    vorher = ft8_native.deckel_treffer
    ft8_native._deckel_pruefen(ft8_native.MAX_DECODES_PER_SLOT - 1)
    assert ft8_native.deckel_treffer == vorher
    ft8_native._deckel_pruefen(ft8_native.MAX_DECODES_PER_SLOT)
    assert ft8_native.deckel_treffer == vorher + 1


def test_audiopuffer_haelt_gleichzeitiges_schreiben_und_lesen_aus() -> None:
    """Aufnahme-Thread und Ereignisschleife teilten sich eine deque ohne Sperre."""
    import threading

    from ft8_appliance.audio.slot_sync import SAMPLE_RATE_HZ, SlotBuffer

    puffer = SlotBuffer(max_age_s=2.0)
    stopp = threading.Event()
    chunk = b"\x00\x00" * 1024

    def _schreiben() -> None:
        t = 1_800_000_000.0
        while not stopp.is_set():
            puffer.feed(chunk, t)
            t += 1024 / SAMPLE_RATE_HZ
    faden = threading.Thread(target=_schreiben)
    faden.start()
    try:
        for i in range(3000):
            puffer.extract_slot(1_800_000_000.0 + i * 0.01, slot_seconds=1.0)
    finally:
        stopp.set()
        faden.join()


# ---------------------------------------------------------------------------
# PSK-Reporter-Upload nach der Spezifikation (pskdev.html)

def _ipfix_lesen(paket: bytes):
    """Kleiner IPFIX-Leser: Kopf, Templates, Datensaetze nach Template."""
    import struct

    version, laenge, _zeit, sequenz, _kennung = struct.unpack(">HHIII", paket[:16])
    assert version == 10 and laenge == len(paket)
    templates: dict[int, list[tuple[int, int]]] = {}
    saetze: dict[int, list[dict[int, object]]] = {}
    pos = 16
    while pos < len(paket):
        set_id, set_len = struct.unpack(">HH", paket[pos:pos + 4])
        rumpf = paket[pos + 4:pos + set_len]
        pos += set_len
        if set_id == 2:
            p = 0
            while p + 4 <= len(rumpf):
                tid, anzahl = struct.unpack(">HH", rumpf[p:p + 4])
                p += 4
                if tid == 0:
                    break
                felder = []
                for _ in range(anzahl):
                    fid, flen = struct.unpack(">HH", rumpf[p:p + 4])
                    p += 4
                    if fid & 0x8000:
                        assert struct.unpack(">I", rumpf[p:p + 4])[0] == 30351
                        p += 4
                    felder.append((fid, flen))
                templates[tid] = felder
        else:
            p = 0
            felder = templates[set_id]
            mindest = sum(1 if flen == 0xFFFF else flen for _, flen in felder)
            while len(rumpf) - p >= mindest and any(rumpf[p:]):
                satz: dict[int, object] = {}
                for fid, flen in felder:
                    if flen == 0xFFFF:
                        n = rumpf[p]
                        satz[fid] = rumpf[p + 1:p + 1 + n].decode()
                        p += 1 + n
                    else:
                        satz[fid] = int.from_bytes(rumpf[p:p + flen], "big", signed=(flen == 1))
                        p += flen
                saetze.setdefault(set_id, []).append(satz)
    return sequenz, templates, saetze


def test_psk_reporter_upload_traegt_die_betriebsart_unter_der_richtigen_nummer() -> None:
    """Bis 2026-11-14 stand die Betriebsart unter 30351.3 (Locator des Senders) und die
    Quelle unter einer Nummer, die es nicht gibt: Die Berichte kamen ohne Betriebsart an."""
    from datetime import UTC, datetime

    from ft8_appliance.integrations.psk_reporter import PskReporterClient, _PendingSpot

    c = PskReporterClient(my_call="DO3XR", my_grid="JN58CH", decoder_software="ft8-appliance 0.196.0")
    jetzt = datetime(2026, 11, 14, 16, 0, tzinfo=UTC)
    spots = [
        _PendingSpot(sender_call="EA3IVP", sender_grid="JN01", snr_db=-16,
                     band_hz=21_140_417, mode="FT4", decoded_at=jetzt),
        _PendingSpot(sender_call="K2EU", sender_grid=None, snr_db=2,
                     band_hz=21_141_200, mode="FT4", decoded_at=jetzt),
    ]
    seq1, templates, saetze = _ipfix_lesen(c._build_ipfix_packet(spots))
    seq2, _, _ = _ipfix_lesen(c._build_ipfix_packet(spots[:1]))
    assert (seq1, seq2) == (0, 2)                 # zaehlt Berichte, nicht Pakete

    sender = saetze[c._TX_TEMPLATE_ID]
    assert [(fid, flen) for fid, flen in templates[c._TX_TEMPLATE_ID]] == [
        (0x8001, 0xFFFF), (0x8005, 4), (0x8006, 1), (0x800A, 0xFFFF),
        (0x8003, 0xFFFF), (0x800B, 1), (150, 4),
    ]
    assert sender[0] == {0x8001: "EA3IVP", 0x8005: 21_140_417, 0x8006: -16, 0x800A: "FT4",
                         0x8003: "JN01", 0x800B: 1, 150: int(jetzt.timestamp())}
    assert sender[1][0x8003] == "" and sender[1][0x8006] == 2
    empfaenger = saetze[c._RX_TEMPLATE_ID][0]
    assert empfaenger[0x8002] == "DO3XR" and empfaenger[0x8004] == "JN58CH"
    assert empfaenger[0x8008] == "ft8-appliance 0.196.0"
    assert empfaenger[0x8009] == ""               # keine erfundene Antennenangabe mehr
