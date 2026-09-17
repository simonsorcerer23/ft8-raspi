"""QSOs unter einer Rufzeichen-Variante (/MM, /AM, EA8/…) bei den Logbuch-Diensten.

QRZ, Club Log, eQSL und LoTW fuehren jede gesendete Variante als eigenes
Rufzeichen mit eigenem Log, Konto oder Zertifikat — laut deren eigener
Dokumentation (recherchiert 2026-09-17). Bis v0.167 landete ein QSO als
DK9XR/MM bei QRZ, Club Log und eQSL trotzdem im Heimat-Log von DK9XR. Nur
LoTW liess es liegen, und meldete das allein im Systemprotokoll.

Die Regel jetzt, fuer alle vier gleich: Heimat-Call ins Heimat-Log, eine
Variante nur in ein ausdruecklich fuer sie eingerichtetes — sonst liegen
lassen, ohne Versuchszaehler, und einmal per Push melden.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from ft8_appliance.config import OperatorConfig
from ft8_appliance.config.models import EqslKonto
from ft8_appliance.db import models as m
from ft8_appliance.db.session import init_engine, session_scope
from ft8_appliance.integrations.eqsl import EqslErgebnis
from ft8_appliance.runtime.orchestrator import Orchestrator

HEIM, SEE = "DK9XR", "DK9XR/MM"


class _Ntfy:
    enabled = True

    def __init__(self) -> None:
        self.gesendet: list[dict] = []

    async def notify(self, msg: str, **kw) -> None:
        self.gesendet.append({"msg": msg, **kw})


def _qso(call: str, station: str, minute: int = 0) -> m.Qso:
    start = datetime(2026, 9, 17, 8, minute, tzinfo=UTC)
    return m.Qso(call=call, band="20m", freq_hz=14_074_000, mode="FT8",
                 rst_sent="-12", rst_rcvd="-08", my_grid="JN58",
                 user_callsign=HEIM, station_callsign=station,
                 qso_start=start, qso_end=start + timedelta(seconds=30))


async def _db(qsos: list[m.Qso]) -> None:
    eng = init_engine(None)
    async with eng.begin() as c:
        await c.run_sync(m.Base.metadata.create_all)
    async with session_scope() as s:
        s.add_all(qsos)


async def _stand() -> dict[str, m.Qso]:
    async with session_scope() as s:
        return {q.call: q for q in (await s.execute(select(m.Qso))).scalars()}


def _stub(op: OperatorConfig, *, ordner=None, ntfy: _Ntfy | None = None) -> SimpleNamespace:
    o = SimpleNamespace(
        config=SimpleNamespace(demo_mode=False, operators=[op], operator=op),
        integrations=SimpleNamespace(ntfy=ntfy),
        _UPLOAD_MAX_ATTEMPTS=Orchestrator._UPLOAD_MAX_ATTEMPTS,
        _EQSL_CHARGE_MAX=Orchestrator._EQSL_CHARGE_MAX,
        _BULK_DUPE_NACHFAHREN_MAX=Orchestrator._BULK_DUPE_NACHFAHREN_MAX,
        _upload_reject_is_hard=Orchestrator._upload_reject_is_hard,
        _upload_reject_ist_dupe=Orchestrator._upload_reject_ist_dupe,
        _alert_upload_giveup=lambda *a, **kw: None,
        _alert_upload_giveup_many=lambda *a, **kw: None,
        _operator_for_qso=lambda qso: op,
        _operator_for_call=lambda call: op,
        _orphan_upload_warned=set(),
        _ohne_einrichtung_gemeldet=None,
        _filter_drops_path=(ordner / "filter_drops.json") if ordner else None,
    )
    for name in ("_melde_ohne_einrichtung", "_ohne_einrichtung_menge",
                 "_ohne_einrichtung_pfad", "_speichere_ohne_einrichtung",
                 "vergiss_ohne_einrichtung", "_eqsl_lade_charge",
                 "_clublog_lade_log", "_clublog_einzelsweep"):
        setattr(o, name, partial(getattr(Orchestrator, name), o))

    async def note(service: str, exc: Exception | None) -> None:
        if exc is not None:
            raise AssertionError(f"{service}-Drain warf: {exc!r}") from exc

    o._note_drain_outcome = note
    return o


async def _ein_sweep(coro, monkeypatch) -> None:
    """Drain-Schleife genau einmal durchlaufen lassen."""
    echt = asyncio.sleep

    class _Stopp(Exception):
        pass

    async def schlaf(delay, *a, **kw):
        if delay >= 60:
            raise _Stopp
        return await echt(0)

    monkeypatch.setattr(asyncio, "sleep", schlaf)
    with pytest.raises(_Stopp):
        await coro
    monkeypatch.setattr(asyncio, "sleep", echt)


# ------------------------------------------------------------------------ QRZ

@pytest.mark.asyncio
async def test_qrz_variante_ohne_logbuch_landet_nicht_im_heimat_logbuch(monkeypatch) -> None:
    await _db([_qso("K1HEIM", HEIM, 0), _qso("K1SEE", SEE, 1)])
    hochgeladen: list[tuple[str, str]] = []

    async def fake(key, my_call, qso):
        hochgeladen.append((key, qso.call))
        return SimpleNamespace(logbook_id="1")

    from ft8_appliance.integrations import qrz_logbook
    monkeypatch.setattr(qrz_logbook, "upload_qso", fake)
    ntfy = _Ntfy()
    op = OperatorConfig(callsign=HEIM, qrz_logbook_api_key="HEIM-KEY")
    o = _stub(op, ntfy=ntfy)

    await _ein_sweep(Orchestrator._qrz_logbook_drain_loop(o), monkeypatch)
    await asyncio.sleep(0)

    assert hochgeladen == [("HEIM-KEY", "K1HEIM")]
    see = (await _stand())["K1SEE"]
    assert see.qrz_uploaded is False
    assert see.qrz_upload_attempts == 0, "fehlende Einrichtung ist kein Fehlversuch"
    assert len(ntfy.gesendet) == 1 and SEE in ntfy.gesendet[0]["msg"]

    # Logbuch eingetragen → laeuft nach, mit dem eigenen Schluessel
    op.qrz_logbooks = {SEE: "SEE-KEY"}
    await _ein_sweep(Orchestrator._qrz_logbook_drain_loop(o), monkeypatch)
    assert ("SEE-KEY", "K1SEE") in hochgeladen


# ------------------------------------------------------------------- Club Log

@pytest.mark.asyncio
async def test_clublog_schickt_die_variante_in_ihr_eigenes_log(monkeypatch) -> None:
    await _db([_qso("K1HEIM", HEIM, 0), _qso("K1SEE", SEE, 1)])
    gesendet: list[tuple[str, str]] = []

    async def fake(email, pw, api, my_call, qso, *, log_callsign=None):
        gesendet.append((log_callsign, qso.call))

    from ft8_appliance.integrations import clublog
    monkeypatch.setattr(clublog, "upload_qso", fake)
    op = OperatorConfig(callsign=HEIM, clublog_email="a@b", clublog_app_password="p",
                        clublog_api_key="k")
    o = _stub(op, ntfy=_Ntfy())

    await Orchestrator._clublog_sweep_for_operator(o, "a@b", "p", "k", HEIM, 5, op=op)
    assert gesendet == [(HEIM, "K1HEIM")], "ohne Eintrag darf /MM nicht ins Heimat-Log"
    assert (await _stand())["K1SEE"].clublog_upload_attempts == 0

    op.clublog_rufzeichen = [SEE]
    await Orchestrator._clublog_sweep_for_operator(o, "a@b", "p", "k", HEIM, 5, op=op)
    assert (SEE, "K1SEE") in gesendet


@pytest.mark.asyncio
async def test_clublog_bulk_trennt_nach_log(monkeypatch) -> None:
    await _db([_qso(f"H{i}", HEIM, i) for i in range(6)]
              + [_qso(f"S{i}", SEE, 10 + i) for i in range(6)])
    chargen: list[tuple[str, list[str]]] = []

    async def fake_bulk(email, pw, api, my_call, qsos, *, log_callsign=None):
        chargen.append((log_callsign, sorted(q.call for q in qsos)))

    from ft8_appliance.integrations import clublog
    monkeypatch.setattr(clublog, "bulk_upload", fake_bulk)
    op = OperatorConfig(callsign=HEIM, clublog_rufzeichen=[SEE])
    o = _stub(op)

    await Orchestrator._clublog_sweep_for_operator(o, "a@b", "p", "k", HEIM, 5, op=op)
    assert sorted(chargen) == [
        (HEIM, [f"H{i}" for i in range(6)]),
        (SEE, [f"S{i}" for i in range(6)]),
    ]


# ------------------------------------------------------------------------ eQSL

@pytest.mark.asyncio
async def test_eqsl_nutzt_das_konto_der_variante(monkeypatch) -> None:
    await _db([_qso("K1HEIM", HEIM, 0), _qso("K1SEE", SEE, 1)])
    anmeldungen: list[tuple[str, list[str]]] = []

    async def fake(user, pw, qsos, **kw):
        anmeldungen.append((user, [q.call for q in qsos]))
        return EqslErgebnis(len(qsos), len(qsos))

    import ft8_appliance.integrations.eqsl as mod
    monkeypatch.setattr(mod, "upload", fake)
    op = OperatorConfig(callsign=HEIM, eqsl_user="dk9xr", eqsl_password="x")
    o = _stub(op, ntfy=_Ntfy())

    await Orchestrator._eqsl_sweep_fuer_operator(o, op)
    assert anmeldungen == [("dk9xr", ["K1HEIM"])]
    assert (await _stand())["K1SEE"].eqsl_upload_attempts == 0

    op.eqsl_konten = {SEE: EqslKonto(user="DK9XR/MM", password="y")}
    await Orchestrator._eqsl_sweep_fuer_operator(o, op)
    assert ("DK9XR/MM", ["K1SEE"]) in anmeldungen


@pytest.mark.asyncio
async def test_eqsl_meldet_ablehnungen_die_keine_duplikate_sind(monkeypatch) -> None:
    """eQSL sagt nicht, welche Datensaetze es abgelehnt hat. Bis v0.167 galt
    die Charge trotzdem stillschweigend als erledigt."""
    await _db([_qso(f"K{i}", HEIM, i) for i in range(3)])
    antworten = [
        EqslErgebnis(1, 3, ("Warning: K1 Duplicate", "Warning: K2 Duplicate")),
        EqslErgebnis(1, 3, ("Warning: K1 Duplicate", "Error: Bad record")),
    ]

    async def fake(user, pw, qsos, **kw):
        return antworten.pop(0)

    import ft8_appliance.integrations.eqsl as mod
    monkeypatch.setattr(mod, "upload", fake)
    op = OperatorConfig(callsign=HEIM, eqsl_user="dk9xr", eqsl_password="x")
    ntfy = _Ntfy()
    o = _stub(op, ntfy=ntfy)

    faellig = list((await _stand()).values())
    await Orchestrator._eqsl_lade_charge(o, op, HEIM, faellig, datetime.now(UTC))
    await asyncio.sleep(0)
    assert ntfy.gesendet == [], "nur Duplikate — nichts verloren"

    await Orchestrator._eqsl_lade_charge(o, op, HEIM, faellig, datetime.now(UTC))
    await asyncio.sleep(0)
    assert len(ntfy.gesendet) == 1
    assert "1 von 3" in ntfy.gesendet[0]["msg"]


# ---------------------------------------------------------------- die Meldung

@pytest.mark.asyncio
async def test_meldung_kommt_einmal_auch_ueber_einen_neustart(tmp_path) -> None:
    """Die Station startet bei jedem Self-Update neu. Dieselbe Push-Nachricht
    nach jedem Update waere Laerm — die Liste liegt deshalb auf der Platte."""
    op = OperatorConfig(callsign=HEIM)
    ntfy = _Ntfy()
    o = _stub(op, ordner=tmp_path, ntfy=ntfy)
    o._melde_ohne_einrichtung("QRZ", HEIM, SEE, 3)
    o._melde_ohne_einrichtung("QRZ", HEIM, SEE, 4)

    neu = _stub(op, ordner=tmp_path, ntfy=ntfy)          # "nach dem Neustart"
    neu._melde_ohne_einrichtung("QRZ", HEIM, SEE, 5)
    neu._melde_ohne_einrichtung("eQSL", HEIM, SEE, 5)     # anderer Dienst: eigene Meldung
    await asyncio.sleep(0)
    assert [g["title"] for g in ntfy.gesendet] == [
        "⚠️ QRZ: DK9XR/MM nicht eingerichtet", "⚠️ eQSL: DK9XR/MM nicht eingerichtet"]

    # Eingerichtet und wieder entfernt: dann soll es wieder gemeldet werden
    neu.vergiss_ohne_einrichtung("QRZ", HEIM, "dk9xr/mm")
    neu._melde_ohne_einrichtung("QRZ", HEIM, SEE, 1)
    await asyncio.sleep(0)
    assert len(ntfy.gesendet) == 3


# --------------------------------------------------------- Operator-Verwaltung

def _orch_fuer_routen(op: OperatorConfig) -> SimpleNamespace:
    async def persist() -> None:
        return None

    return SimpleNamespace(
        config=SimpleNamespace(operators=[op], active_callsign=HEIM),
        persist_config=persist,
        vergiss_ohne_einrichtung=lambda *a: None,
        starte_eqsl_loop_falls_noetig=lambda *a: False,
    )


@pytest.mark.asyncio
async def test_verwaltung_nimmt_varianten_aber_nicht_den_heimat_call() -> None:
    from ft8_appliance.web.routes import operators as r

    op = OperatorConfig(callsign=HEIM, eqsl_user="dk9xr", eqsl_password="x")
    orch = _orch_fuer_routen(op)

    with pytest.raises(HTTPException) as fehler:
        await r.add_clublog_rufzeichen(HEIM, r.ClublogRufzeichenRequest(on_air_call="dk9xr"), orch)
    assert fehler.value.status_code == 400

    aus = await r.add_clublog_rufzeichen(HEIM, r.ClublogRufzeichenRequest(on_air_call="dk9xr/mm"), orch)
    assert aus.clublog_rufzeichen == [SEE]

    aus = await r.upsert_eqsl_konto(HEIM, r.EqslKontoRequest(
        on_air_call=SEE, eqsl_user="DK9XR/MM", eqsl_password="GEHEIM"), orch)
    assert aus.eqsl_konten == [SEE]
    assert "GEHEIM" not in aus.model_dump_json(), "Passwort darf die API nie verlassen"
    assert op.eqsl_konto_for(SEE) == ("DK9XR/MM", "GEHEIM", None)


@pytest.mark.asyncio
async def test_vorabpruefung_warnt_vor_der_ersten_verbindung() -> None:
    """Wer auf /MM umschaltet, soll es VOR dem ersten liegengebliebenen QSO
    erfahren. Beide Zweige kommen hier ohne Netz aus."""
    op = OperatorConfig(callsign=HEIM, clublog_email="a@b", clublog_app_password="p",
                        clublog_api_key="k")
    o = _stub(op)
    ergebnis = await Orchestrator.check_call_setup(o, SEE)
    assert ergebnis["qrz"]["status"] == "not_set_up"
    assert ergebnis["clublog"]["status"] == "not_set_up"
    assert "Settings → Callsigns" in ergebnis["clublog"]["detail"]
