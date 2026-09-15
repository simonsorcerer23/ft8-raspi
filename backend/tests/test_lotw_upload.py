"""LoTW-Upload ueber TQSL: ADIF, Statusauswertung, Drain-Loop.

Der Kern sind die Exit-Codes. TQSL meldet 8 fuer "alles waren Duplikate"
und 9 fuer "teils Duplikate, Rest hochgeladen" — beides ist Erfolg. Wer
nur auf 0 prueft, haelt gelungene Uploads fuer gescheitert und laedt
ewig dieselben QSOs erneut hoch.
"""
from __future__ import annotations

import ast
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from ft8_appliance.config import OperatorConfig
from ft8_appliance.db import models as m
from ft8_appliance.db.session import init_engine, session_scope
from ft8_appliance.integrations.lotw import (
    ERFOLG, HART, LotwError, LotwErgebnis, baue_adif, lies_status, upload,
)
from ft8_appliance.runtime.orchestrator import Orchestrator

WURZEL = Path(__file__).resolve().parents[1]


def _qso(n: int = 1, **kw) -> m.Qso:
    felder = dict(
        call=f"OH3OJ{n}", band="20m", freq_hz=14074000, mode="FT8",
        rst_sent="-12", rst_rcvd="-08", my_grid="JN58td", user_callsign="DK9XR",
        station_callsign="DK9XR",
        qso_start=datetime(2026, 9, 14, 8, n % 60, 45, tzinfo=UTC),
        qso_end=datetime(2026, 9, 14, 8, n % 60, 59, tzinfo=UTC),
    )
    felder.update(kw)
    return m.Qso(**felder)


# ------------------------------------------------------------------ ADIF

def test_adif_traegt_die_pflichtfelder() -> None:
    """CALL, MODE, QSO_DATE, TIME_ON und BAND verlangt die ARRL."""
    t = baue_adif([_qso()])
    for erwartet in ("<call:6>OH3OJ1", "<qso_date:8>20260914", "<band:3>20m",
                     "<mode:3>FT8", "<station_callsign:5>DK9XR"):
        assert erwartet in t, erwartet
    assert "<eor>" in t and "<eoh>" in t


def test_zeit_mit_sekunden() -> None:
    """LoTW gleicht Zeiten ab; die Sekunde kostet nichts und hilft."""
    assert "<time_on:6>080145" in baue_adif([_qso(1)])


def test_keine_my_felder() -> None:
    """TQSL nimmt den Standort aus der Station Location und prueft
    mitgeschickte MY_-Felder dagegen — mitschicken bringt nur Konflikte."""
    t = baue_adif([_qso()])
    assert "my_grid" not in t and "my_dxcc" not in t


# ------------------------------------------------------------ Exit-Codes

def test_duplikat_codes_gelten_als_erfolg() -> None:
    assert {0, 8, 9} == set(ERFOLG)
    assert 8 in ERFOLG and 9 in ERFOLG, "sonst laedt der Loop ewig dasselbe hoch"


def test_netzfehler_ist_nicht_hart() -> None:
    """Code 11 ist LoTW nicht erreichbar — morgen geht es wieder."""
    assert 11 not in HART and 11 not in ERFOLG


def test_zurueckgewiesen_ist_hart() -> None:
    assert 2 in HART and 4 in HART and 5 in HART


def test_statuszeile_wird_gelesen() -> None:
    meldung, code = lies_status(
        "17:04:13 Final Status: Some QSOs were duplicates (9)\n", 0)
    assert code == 9 and "duplicate" in meldung.lower()


def test_statuszeile_schlaegt_den_exitcode() -> None:
    """Die ARRL dokumentiert die Zahl in der Statuszeile, nicht den
    Prozess-Exitcode. Wo beide widersprechen, gilt die Zeile."""
    _, code = lies_status("Final Status: Success (0)", 9)
    assert code == 0


def test_ohne_statuszeile_gilt_der_exitcode() -> None:
    meldung, code = lies_status("irgendein Murks\n", 11)
    assert code == 11 and "Murks" in meldung


# ---------------------------------------------------------------- Upload

@pytest.mark.asyncio
async def test_leere_liste_ruft_tqsl_nicht_auf() -> None:
    e = await upload([], station_location="Home")
    assert e.gesamt == 0


@pytest.mark.asyncio
async def test_fehlendes_tqsl_ist_hart(monkeypatch) -> None:
    import ft8_appliance.integrations.lotw as mod
    monkeypatch.setattr(mod.shutil, "which", lambda n: None)
    with pytest.raises(LotwError) as exc:
        await upload([_qso()], station_location="Home")
    assert exc.value.hart is True


@pytest.mark.asyncio
async def test_aufruf_ist_dialogfrei(monkeypatch) -> None:
    """Vier Dialogquellen muessen abgedeckt sein, sonst haengt der Lauf
    unbeaufsichtigt: Datumsbereich, Duplikate, Station Location, Passwort."""
    gesehen: dict = {}

    class _Proc:
        returncode = 0
        async def communicate(self):
            return b"", b"Final Status: Success (0)"

    async def _exec(*befehl, **kw):
        gesehen["befehl"] = list(befehl)
        return _Proc()

    import ft8_appliance.integrations.lotw as mod
    monkeypatch.setattr(mod.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", _exec)
    await upload([_qso()], station_location="Mein QTH", cert_password="geheim")
    b = gesehen["befehl"]
    assert b[:2] == ["xvfb-run", "-a"], "ohne virtuelles Display startet TQSL nicht"
    assert "-d" in b, "Datumsdialog"
    assert b[b.index("-a", 2) + 1] == "compliant", "Duplikatdialog"
    assert b[b.index("-l") + 1] == "Mein QTH"
    assert b[b.index("-p") + 1] == "geheim"
    assert "-u" in b and "-x" in b, "signieren, hochladen, beenden"


@pytest.mark.asyncio
async def test_ohne_passphrase_kein_p_schalter(monkeypatch) -> None:
    gesehen: dict = {}

    class _Proc:
        returncode = 8
        async def communicate(self):
            return b"", b"Final Status: No QSOs to upload (8)"

    async def _exec(*befehl, **kw):
        gesehen["befehl"] = list(befehl)
        return _Proc()

    import ft8_appliance.integrations.lotw as mod
    monkeypatch.setattr(mod.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", _exec)
    e = await upload([_qso()], station_location="Home")
    assert "-p" not in gesehen["befehl"]
    assert e.alles_duplikate and e.code == 8


@pytest.mark.asyncio
async def test_harter_code_wirft(monkeypatch) -> None:
    class _Proc:
        returncode = 2
        async def communicate(self):
            return b"", b"Final Status: Rejected by LoTW (2)"

    async def _exec(*b, **kw):
        return _Proc()

    import ft8_appliance.integrations.lotw as mod
    monkeypatch.setattr(mod.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", _exec)
    with pytest.raises(LotwError) as exc:
        await upload([_qso()], station_location="Home")
    assert exc.value.hart is True and exc.value.code == 2


# ------------------------------------------------------------ Drain-Loop

async def _db(qsos: list[m.Qso]) -> None:
    eng = init_engine(None)
    async with eng.begin() as c:
        await c.run_sync(m.Base.metadata.create_all)
    async with session_scope() as s:
        for q in qsos:
            s.add(q)


def _orch(ergebnis=None, fehler=None, *, monkeypatch) -> SimpleNamespace:
    op = OperatorConfig(callsign="DK9XR", lotw_station_location="Home")
    gesehen: list = []

    async def _upload(qsos, **kw):
        gesehen.append((list(qsos), kw))
        if fehler is not None:
            raise fehler
        return ergebnis or LotwErgebnis(0, "Success", len(qsos))

    import ft8_appliance.integrations.lotw as mod
    monkeypatch.setattr(mod, "upload", _upload)
    o = SimpleNamespace(
        config=SimpleNamespace(operators=[op], demo_mode=False),
        _LOTW_CHARGE_MAX=Orchestrator._LOTW_CHARGE_MAX,
        _UPLOAD_MAX_ATTEMPTS=Orchestrator._UPLOAD_MAX_ATTEMPTS,
        _alert_upload_giveup_many=lambda *a: None,
        _lotw_gemeldet=set(),
    )
    o._lotw_melde_fehlende_location = \
        lambda *a: Orchestrator._lotw_melde_fehlende_location(o, *a)
    o._lotw_lade_charge = lambda *a: Orchestrator._lotw_lade_charge(o, *a)
    o.gesehen, o.op = gesehen, op
    return o


async def _stand() -> list[tuple]:
    async with session_scope() as s:
        return [(q.call, q.lotw_uploaded, q.lotw_upload_attempts)
                for q in (await s.execute(select(m.Qso).order_by(m.Qso.call))).scalars()]


@pytest.mark.asyncio
async def test_erfolg_markiert_die_charge(monkeypatch) -> None:
    await _db([_qso(i) for i in range(3)])
    o = _orch(monkeypatch=monkeypatch)
    await Orchestrator._lotw_sweep_fuer_operator(o, o.op)
    assert all(hoch for _, hoch, _ in await _stand())


@pytest.mark.asyncio
async def test_alles_duplikate_gilt_als_erledigt(monkeypatch) -> None:
    """Code 8 heisst: LoTW hat sie schon. Nicht ewig erneut schicken."""
    await _db([_qso(i) for i in range(3)])
    o = _orch(ergebnis=LotwErgebnis(8, "No QSOs to upload", 3), monkeypatch=monkeypatch)
    await Orchestrator._lotw_sweep_fuer_operator(o, o.op)
    assert all(hoch for _, hoch, _ in await _stand())


@pytest.mark.asyncio
async def test_harter_fehler_verbucht_nichts(monkeypatch) -> None:
    await _db([_qso(1, lotw_upload_attempts=Orchestrator._UPLOAD_MAX_ATTEMPTS - 1)])
    o = _orch(fehler=LotwError("Rejected", hart=True, code=2), monkeypatch=monkeypatch)
    await Orchestrator._lotw_sweep_fuer_operator(o, o.op)
    assert [hoch for _, hoch, _ in await _stand()] == [False]


@pytest.mark.asyncio
async def test_netzfehler_laesst_offen(monkeypatch) -> None:
    await _db([_qso(i) for i in range(3)])
    o = _orch(fehler=LotwError("LoTW nicht erreichbar", code=11), monkeypatch=monkeypatch)
    await Orchestrator._lotw_sweep_fuer_operator(o, o.op)
    assert not any(hoch for _, hoch, _ in await _stand())


@pytest.mark.asyncio
async def test_backoff_und_charge(monkeypatch) -> None:
    gerade = datetime.now(UTC) - timedelta(seconds=60)
    await _db([_qso(1, lotw_upload_attempts=1, lotw_last_attempt_at=gerade), _qso(2)])
    o = _orch(monkeypatch=monkeypatch)
    await Orchestrator._lotw_sweep_fuer_operator(o, o.op)
    assert [q.call for q in o.gesehen[0][0]] == ["OH3OJ2"]


@pytest.mark.asyncio
async def test_nur_eigene_qsos(monkeypatch) -> None:
    await _db([_qso(1), _qso(2, user_callsign="DO3XR")])
    o = _orch(monkeypatch=monkeypatch)
    await Orchestrator._lotw_sweep_fuer_operator(o, o.op)
    assert [q.call for q in o.gesehen[0][0]] == ["OH3OJ1"]


# ----------------------------------------------- Location je Sende-Call

def test_heimat_call_nimmt_die_heimat_location() -> None:
    op = OperatorConfig(callsign="DK9XR", lotw_station_location="Weissenhorn")
    assert op.lotw_location_for("DK9XR") == "Weissenhorn"
    assert op.lotw_location_for(None) == "Weissenhorn"


def test_fremder_call_ohne_eintrag_bekommt_nichts() -> None:
    """Der entscheidende Unterschied zu qrz_key_for: KEIN Rueckfall.

    Eine Station Location traegt ein festes DXCC und einen festen Grid.
    /MM und /AM haben ueberhaupt kein DXCC — sie mit der Heimat-Location
    zu signieren waere eine falsche Aussage gegenueber LoTW, und anders
    als ein QSO im falschen QRZ-Logbuch bekommt man das nicht zurueck.
    """
    op = OperatorConfig(callsign="DK9XR", lotw_station_location="Weissenhorn")
    assert op.lotw_location_for("DK9XR/MM") is None
    assert op.lotw_location_for("9A/DK9XR") is None


def test_eigener_eintrag_gewinnt() -> None:
    op = OperatorConfig(callsign="DK9XR", lotw_station_location="Weissenhorn",
                        lotw_station_locations={"dk9xr/mm": "Schiff"})
    assert op.lotw_location_for("DK9XR/MM") == "Schiff"
    assert op.lotw_location_for("DK9XR") == "Weissenhorn"


@pytest.mark.asyncio
async def test_zwei_sende_calls_werden_getrennt_signiert(monkeypatch) -> None:
    await _db([_qso(1), _qso(2, station_callsign="DK9XR/MM")])
    o = _orch(monkeypatch=monkeypatch)
    o.op.lotw_station_locations = {"DK9XR/MM": "Schiff"}
    await Orchestrator._lotw_sweep_fuer_operator(o, o.op)
    orte = {kw["station_location"]: [q.call for q in qsos]
            for qsos, kw in o.gesehen}
    assert orte == {"Home": ["OH3OJ1"], "Schiff": ["OH3OJ2"]}


@pytest.mark.asyncio
async def test_ohne_location_bleibt_liegen_statt_falsch_signiert(monkeypatch) -> None:
    await _db([_qso(1), _qso(2, station_callsign="DK9XR/MM")])
    o = _orch(monkeypatch=monkeypatch)
    await Orchestrator._lotw_sweep_fuer_operator(o, o.op)
    assert [q.call for qsos, _ in o.gesehen for q in qsos] == ["OH3OJ1"]
    assert await _stand() == [("OH3OJ1", True, 1), ("OH3OJ2", False, 0)]


@pytest.mark.asyncio
async def test_liegengebliebene_blockieren_die_charge_nicht(monkeypatch) -> None:
    """Ohne Versuchszaehler stehen sie fuer immer vorn in der Liste.

    Zaehlten sie gegen die Chargengrenze, wuerden ein paar hundert
    /MM-QSOs jeden Sweep fuellen und nichts Neues kaeme je an die Reihe.
    """
    await _db([_qso(i, station_callsign="DK9XR/MM") for i in range(3)]
              + [_qso(9)])
    o = _orch(monkeypatch=monkeypatch)
    o._LOTW_CHARGE_MAX = 3
    await Orchestrator._lotw_sweep_fuer_operator(o, o.op)
    assert [q.call for qsos, _ in o.gesehen for q in qsos] == ["OH3OJ9"]


@pytest.mark.asyncio
async def test_meldung_kommt_nur_einmal(monkeypatch) -> None:
    """Sonst steht die Meldung alle 30 Minuten im Journal.

    Gezaehlt wird am Logger selbst, nicht ueber caplog: der Sweep
    protokolliert ueber den Modul-Logger, und dessen Weiterleitung
    haengt davon ab, was andere Tests vorher an der Log-Einrichtung
    gedreht haben — in der Gesamtsuite kam mit caplog nichts an.
    """
    await _db([_qso(1, station_callsign="DK9XR/MM")])
    o = _orch(monkeypatch=monkeypatch)
    meldungen: list[str] = []
    import ft8_appliance.runtime.orchestrator as mod
    monkeypatch.setattr(mod.log, "error",
                        lambda msg, *a, **kw: meldungen.append(msg % a if a else msg))
    await Orchestrator._lotw_sweep_fuer_operator(o, o.op)
    await Orchestrator._lotw_sweep_fuer_operator(o, o.op)
    assert sum("DK9XR/MM" in m for m in meldungen) == 1


# ------------------------------------------------------------ Verdrahtung

def test_alle_konfigurationswege_werfen_den_loop_an() -> None:
    quelle = (WURZEL / "ft8_appliance" / "runtime" / "orchestrator.py").read_text()
    assert quelle.count("self._spawn(self._lotw_drain_loop()") == 1
    rufer = set()
    for knoten in ast.walk(ast.parse(quelle)):
        if not isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(knoten):
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "starte_lotw_loop_falls_noetig"):
                rufer.add(knoten.name)
    assert "reload_active_operator_integrations" in rufer, sorted(rufer)
    assert "on_config_changed" in rufer, sorted(rufer)
    assert len(rufer) == 3, sorted(rufer)


def test_zertifikatspasswort_verlaesst_die_api_nie() -> None:
    quelle = (WURZEL / "ft8_appliance" / "web" / "routes" / "operators.py").read_text()
    block = quelle[quelle.index("class OperatorOut"):quelle.index("class OperatorsResponse")]
    assert "lotw_station_location" in block
    assert "lotw_cert_password" not in block


def test_startfunktion_prueft_einrichtung_und_doppelstart() -> None:
    """Ohne Station Location kann TQSL gar nicht signieren — ein Loop,
    der trotzdem laeuft, produziert nur alle halbe Stunde einen Fehler."""
    async def _nichts() -> None:
        return None

    gespawnt: list[str] = []

    def _spawn(coro, *, name):
        coro.close()
        gespawnt.append(name)
        o._bg_tasks.append(SimpleNamespace(get_name=lambda: name, done=lambda: False))

    ohne = OperatorConfig(callsign="DO3XR")
    mit = OperatorConfig(callsign="DK9XR", lotw_station_location="Home")
    o = SimpleNamespace(db_enabled=True, _bg_tasks=[], _spawn=_spawn,
                        config=SimpleNamespace(operators=[ohne]))
    o._lotw_drain_loop = lambda: _nichts()

    assert Orchestrator.starte_lotw_loop_falls_noetig(o, "test") is False
    assert gespawnt == [], "ohne Station Location darf nichts starten"

    o.config.operators = [ohne, mit]
    assert Orchestrator.starte_lotw_loop_falls_noetig(o, "test") is True
    assert gespawnt == ["lotw-drain"]
    assert Orchestrator.starte_lotw_loop_falls_noetig(o, "test") is False, \
        "ein laufender Loop darf nicht doppelt starten"

    o.db_enabled = False
    o._bg_tasks.clear()
    assert Orchestrator.starte_lotw_loop_falls_noetig(o, "test") is False
