"""Monatsbericht: Text, Sendefenster, Stempel, Zahlen aus der DB."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from ft8_appliance.analyse.monatsbericht import Monatszahlen, baue_monatsbericht
from ft8_appliance.runtime.orchestrator import Orchestrator


def _z(**k) -> Monatszahlen:
    basis = dict(tage=30, std_regel=600.0, qsos_regel=1200, std_kontrolle=66.0,
                 qsos_kontrolle=90, std_gesamt=666.0, qsos_gesamt=1290, std_leerlauf=300.0)
    basis.update(k)
    return Monatszahlen(**basis)


def test_bericht_enthaelt_alle_drei_bloecke() -> None:
    t = baue_monatsbericht(_z(), date(2026, 9, 14))
    assert "push.monatspruefung_kontrolle" in t
    assert "2.00/h" in t and "1.36/h" in t
    assert "push.monatspruefung_regeln (3)" in t
    assert "snr_floor" in t and "pile_up" in t
    assert "Leerlauf 45 %" in t


def test_bericht_urteilt_mit_mindestfallzahl() -> None:
    assert "zu wenig" in baue_monatsbericht(_z(qsos_kontrolle=3), date(2026, 9, 14))
    assert "Rauschen" in baue_monatsbericht(_z(qsos_kontrolle=130), date(2026, 9, 14))


def test_bericht_nennt_den_ew_arm_wenn_er_zeit_hatte() -> None:
    t = baue_monatsbericht(_z(std_ew=300.0, qsos_ew=660), date(2026, 9, 14))
    assert "EW-Modell: 660 QSOs / 300 h = 2.20/h" in t
    assert "EW-Modell" not in baue_monatsbericht(_z(), date(2026, 9, 14))


def test_bericht_ohne_zeitprotokoll_stuerzt_nicht() -> None:
    t = baue_monatsbericht(_z(std_regel=0.0, std_kontrolle=0.0, std_gesamt=0.0), date(2026, 9, 14))
    assert "noch keine Stunden je Arm" in t and "noch kein Zeitprotokoll" in t


def test_ueberschriften_laufen_ueber_die_uebersetzung() -> None:
    t = baue_monatsbericht(_z(), date(2026, 9, 14), lambda k: k.upper())
    assert "PUSH.MONATSPRUEFUNG_ZEIT" in t


# ------------------------------------------------------------ Fenster + Stempel

def _o(tmp_path: Path) -> SimpleNamespace:
    o = SimpleNamespace(_filter_drops_path=tmp_path / "filter_drops.json")
    o._monatsbericht_stempel = lambda: Orchestrator._monatsbericht_stempel(o)
    return o


def test_faellig_nur_am_ersten_im_fenster(tmp_path) -> None:
    o = _o(tmp_path)
    f = lambda d, h, m: Orchestrator._monatsbericht_faellig(o, datetime(2026, 10, d, h, m))  # noqa: E731
    assert f(1, 8, 15) and f(1, 8, 25)
    assert not f(1, 8, 14) and not f(1, 8, 26) and not f(2, 8, 20)


def test_stempel_verhindert_zweiten_versand(tmp_path) -> None:
    """Der Stempel liegt auf Platte — ein Neustart im Fenster meldet
    nicht noch einmal."""
    o = _o(tmp_path)
    Orchestrator._monatsbericht_stempel(o).write_text("2026-10", encoding="utf-8")
    assert not Orchestrator._monatsbericht_faellig(o, datetime(2026, 10, 1, 8, 20))
    assert Orchestrator._monatsbericht_faellig(o, datetime(2026, 11, 1, 8, 20))


@pytest.mark.asyncio
async def test_senden_schreibt_stempel_und_ruft_ntfy(tmp_path) -> None:
    gesendet: list[tuple] = []

    class _Ntfy:
        enabled = True
        async def notify(self, text, **kw):
            gesendet.append((text, kw.get("title")))
            return True

    async def _zahlen(_tage):
        return _z()
    o = SimpleNamespace(
        _filter_drops_path=tmp_path / "filter_drops.json",
        _MONATSBERICHT_TAGE=30,
        _hole_monatszahlen=_zahlen,
        integrations=SimpleNamespace(ntfy=_Ntfy()),
    )
    o._monatsbericht_stempel = lambda: Orchestrator._monatsbericht_stempel(o)
    await Orchestrator._sende_monatsbericht(o, datetime(2026, 10, 1, 8, 20))
    assert len(gesendet) == 1 and "QSOs" in gesendet[0][0]
    assert (tmp_path / "monatsbericht.stamp").read_text() == "2026-10"


@pytest.mark.asyncio
async def test_zahlen_kommen_aus_der_db() -> None:
    from ft8_appliance.db import models as m
    from ft8_appliance.db.session import init_engine, session_scope
    eng = init_engine(None)
    async with eng.begin() as c:
        await c.run_sync(m.Base.metadata.create_all)
    async with session_scope() as s:
        for z, sek in (("IDLE", 3600.0), ("QSO_RESPOND", 7200.0),
                       ("ARM_REGEL", 5400.0), ("ARM_EW", 4320.0), ("ARM_KONTROLLE", 1080.0)):
            s.add(m.StateTimeDaily(tag=date.today().isoformat(), zustand=z, sekunden=sek))
        jetzt = datetime.now(UTC) - timedelta(hours=1)
        for call, outc, arm in (("A", "completed", False), ("B", "completed", False),
                                ("C", "bailed", False), ("D", "completed", True)):
            s.add(m.PickAttempt(ts=jetzt, target_call=call, outcome=outc,
                                kontroll_arm=arm, psk_heard_us=False))
        s.add(m.PickAttempt(ts=jetzt, target_call="E", outcome="completed",
                            kontroll_arm=False, ew_arm=True, psk_heard_us=False))
    z = await Orchestrator._hole_monatszahlen(SimpleNamespace(), 30)
    assert (z.std_regel, z.std_ew, z.std_kontrolle) == (1.5, 1.2, 0.3)
    assert (z.qsos_regel, z.qsos_ew, z.qsos_kontrolle) == (2, 1, 1)
    assert z.std_gesamt == 3.0 and z.std_leerlauf == 1.0
