"""eQSL-Upload im Betrieb: Migration, Drain-Loop, Operator-API.

Der Kern ist der Umgang mit einer Schnittstelle, die nur Zahlen meldet:
Eine angenommene Charge gilt geschlossen als erledigt, ein hartes Nein
darf dagegen nichts als erledigt verbuchen.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from ft8_appliance.config import OperatorConfig
from ft8_appliance.db import models as m
from ft8_appliance.db.session import create_all, init_engine, session_scope
from ft8_appliance.integrations.eqsl import EqslError, EqslErgebnis
from ft8_appliance.runtime.orchestrator import Orchestrator

WURZEL = Path(__file__).resolve().parents[1]


def _qso(n: int, call: str = "OH3OJ", **kw) -> m.Qso:
    felder = dict(
        call=f"{call}{n}", band="20m", freq_hz=14074000, mode="FT8",
        rst_sent="-12", rst_rcvd="-08", my_grid="JN58td", user_callsign="DK9XR",
        qso_start=datetime(2026, 9, 14, 8, n % 60, tzinfo=UTC),
        qso_end=datetime(2026, 9, 14, 8, n % 60, 30, tzinfo=UTC),
    )
    felder.update(kw)
    return m.Qso(**felder)


async def _db(qsos: list[m.Qso]) -> None:
    eng = init_engine(None)
    async with eng.begin() as c:
        await c.run_sync(m.Base.metadata.create_all)
    async with session_scope() as s:
        for q in qsos:
            s.add(q)


def _orch(ergebnis=None, fehler=None, monkeypatch=None) -> SimpleNamespace:
    op = OperatorConfig(callsign="DK9XR", eqsl_user="DK9XR", eqsl_password="geheim")
    gesehen: list = []

    async def _upload(user, pw, qsos, **kw):
        gesehen.append((user, pw, list(qsos), kw))
        if fehler is not None:
            raise fehler
        return ergebnis or EqslErgebnis(len(qsos), len(qsos))

    import ft8_appliance.integrations.eqsl as mod
    monkeypatch.setattr(mod, "upload", _upload)
    o = SimpleNamespace(
        config=SimpleNamespace(operators=[op], demo_mode=False),
        _EQSL_CHARGE_MAX=Orchestrator._EQSL_CHARGE_MAX,
        _UPLOAD_MAX_ATTEMPTS=Orchestrator._UPLOAD_MAX_ATTEMPTS,
        _alert_upload_giveup_many=lambda *a: None,
    )
    o.gesehen = gesehen
    o.op = op
    return o


async def _stand() -> list[tuple]:
    async with session_scope() as s:
        return [(q.call, q.eqsl_uploaded, q.eqsl_upload_attempts)
                for q in (await s.execute(select(m.Qso).order_by(m.Qso.call))).scalars()]


# ------------------------------------------------------------- Migration

@pytest.mark.asyncio
async def test_migration_zieht_die_spalten_nach(tmp_path) -> None:
    """Eine DB von vor v0.152.0 hat die Spalten nicht — Bestandszeilen
    muessen die Migration ueberleben und mit uploaded=0 starten."""
    import sqlite3
    pfad = tmp_path / "alt.sqlite"
    init_engine(pfad)
    await create_all("DK9XR")
    async with session_scope() as s:
        s.add(_qso(1))
    con = sqlite3.connect(pfad)
    # Index zuerst: SQLite laesst eine indizierte Spalte nicht fallen.
    con.execute("drop index if exists ix_qso_eqsl_uploaded")
    for c in ("eqsl_uploaded", "eqsl_upload_attempts", "eqsl_last_attempt_at"):
        con.execute(f"alter table qso drop column {c}")
    con.commit(); con.close()

    init_engine(pfad)
    await create_all("DK9XR")
    con = sqlite3.connect(pfad)
    cols = {r[1] for r in con.execute("pragma table_info(qso)")}
    assert {"eqsl_uploaded", "eqsl_upload_attempts", "eqsl_last_attempt_at"} <= cols
    assert con.execute("select call, eqsl_uploaded, eqsl_upload_attempts from qso").fetchall() \
        == [("OH3OJ1", 0, 0)]


# ------------------------------------------------------------ Drain-Loop

@pytest.mark.asyncio
async def test_erfolg_markiert_die_ganze_charge(monkeypatch) -> None:
    await _db([_qso(i) for i in range(3)])
    o = _orch(monkeypatch=monkeypatch)
    await Orchestrator._eqsl_sweep_fuer_operator(o, o.op)
    assert all(hoch for _, hoch, _ in await _stand())
    assert len(o.gesehen[0][2]) == 3


@pytest.mark.asyncio
async def test_teilweise_angenommen_gilt_trotzdem_als_erledigt(monkeypatch) -> None:
    """eQSL sagt nicht, WELCHE Datensaetze fehlten. Der haeufigste Grund
    ist das Duplikat — und das liegt bereits dort."""
    await _db([_qso(i) for i in range(5)])
    o = _orch(ergebnis=EqslErgebnis(3, 5, ("Warning: Bad record: Duplicate",)),
              monkeypatch=monkeypatch)
    await Orchestrator._eqsl_sweep_fuer_operator(o, o.op)
    assert all(hoch for _, hoch, _ in await _stand())


@pytest.mark.asyncio
async def test_falsches_passwort_verbucht_nichts(monkeypatch) -> None:
    """Sonst waeren die QSOs nach dem Richten der Zugangsdaten still
    uebersprungen — der teuerste denkbare Fehler an dieser Stelle."""
    await _db([_qso(i) for i in range(3)])
    o = _orch(fehler=EqslError("No match on eQSL_User/eQSL_Pswd", hart=True),
              monkeypatch=monkeypatch)
    await Orchestrator._eqsl_sweep_fuer_operator(o, o.op)
    assert not any(hoch for _, hoch, _ in await _stand())
    assert all(v == 1 for _, _, v in await _stand())


@pytest.mark.asyncio
async def test_falsches_passwort_gibt_auch_spaeter_nicht_auf(monkeypatch) -> None:
    """Der Unterschied zum weichen Fehler wird erst am Zaehler sichtbar:
    Ein Netzfehler gibt nach genug Versuchen auf, ein falsches Passwort
    nie — sonst waeren die QSOs weg, sobald jemand es richtet."""
    await _db([_qso(1, eqsl_upload_attempts=Orchestrator._UPLOAD_MAX_ATTEMPTS - 1)])
    o = _orch(fehler=EqslError("No match on eQSL_User/eQSL_Pswd", hart=True),
              monkeypatch=monkeypatch)
    await Orchestrator._eqsl_sweep_fuer_operator(o, o.op)
    assert [hoch for _, hoch, _ in await _stand()] == [False]


@pytest.mark.asyncio
async def test_netzfehler_laesst_offen(monkeypatch) -> None:
    await _db([_qso(i) for i in range(3)])
    o = _orch(fehler=EqslError("Netzfehler: weg"), monkeypatch=monkeypatch)
    await Orchestrator._eqsl_sweep_fuer_operator(o, o.op)
    assert not any(hoch for _, hoch, _ in await _stand())


@pytest.mark.asyncio
async def test_nach_zu_vielen_versuchen_aufgeben(monkeypatch) -> None:
    await _db([_qso(1, eqsl_upload_attempts=Orchestrator._UPLOAD_MAX_ATTEMPTS - 1)])
    o = _orch(fehler=EqslError("Netzfehler: weg"), monkeypatch=monkeypatch)
    await Orchestrator._eqsl_sweep_fuer_operator(o, o.op)
    assert [hoch for _, hoch, _ in await _stand()] == [True]


@pytest.mark.asyncio
async def test_backoff_ueberspringt_frische_versuche(monkeypatch) -> None:
    gerade = datetime.now(UTC) - timedelta(seconds=30)
    await _db([_qso(1, eqsl_upload_attempts=1, eqsl_last_attempt_at=gerade), _qso(2)])
    o = _orch(monkeypatch=monkeypatch)
    await Orchestrator._eqsl_sweep_fuer_operator(o, o.op)
    assert [q.call for q in o.gesehen[0][2]] == ["OH3OJ2"]


@pytest.mark.asyncio
async def test_charge_ist_begrenzt(monkeypatch) -> None:
    await _db([_qso(i) for i in range(Orchestrator._EQSL_CHARGE_MAX + 20)])
    o = _orch(monkeypatch=monkeypatch)
    await Orchestrator._eqsl_sweep_fuer_operator(o, o.op)
    assert len(o.gesehen[0][2]) == Orchestrator._EQSL_CHARGE_MAX


@pytest.mark.asyncio
async def test_nur_eigene_qsos(monkeypatch) -> None:
    """DK9XR-Karten gehen in Raymonds Konto, DO3XR-Karten nicht."""
    await _db([_qso(1), _qso(2, user_callsign="DO3XR")])
    o = _orch(monkeypatch=monkeypatch)
    await Orchestrator._eqsl_sweep_fuer_operator(o, o.op)
    assert [q.call for q in o.gesehen[0][2]] == ["OH3OJ1"]


@pytest.mark.asyncio
async def test_nickname_wird_durchgereicht(monkeypatch) -> None:
    await _db([_qso(1)])
    o = _orch(monkeypatch=monkeypatch)
    o.op = o.op.model_copy(update={"eqsl_qth_nickname": "Home"})
    await Orchestrator._eqsl_sweep_fuer_operator(o, o.op)
    assert o.gesehen[0][3]["qth_nickname"] == "Home"


# --------------------------------------------------------- Verdrahtung

def test_loop_startet_nur_mit_zugangsdaten() -> None:
    quelle = (WURZEL / "ft8_appliance" / "runtime" / "orchestrator.py").read_text()
    assert re.search(r"o\.eqsl_user and o\.eqsl_password for o in self\.config\.operators", quelle)
    assert quelle.count('self._spawn(self._eqsl_drain_loop(), name="eqsl-drain")') == 1


def test_operator_api_gibt_das_passwort_nie_heraus() -> None:
    quelle = (WURZEL / "ft8_appliance" / "web" / "routes" / "operators.py").read_text()
    block = quelle[quelle.index("class OperatorOut"):quelle.index("class OperatorsResponse")]
    assert "eqsl_user" in block and "has_eqsl_credentials" in block
    assert "eqsl_password" not in block, "Passwort darf nicht in der Antwort stehen"


def test_zugangsdaten_lassen_sich_loeschen() -> None:
    from ft8_appliance.web.routes.operators import _NULLABLE_OPERATOR_FIELDS
    assert {"eqsl_user", "eqsl_password", "eqsl_qth_nickname"} <= _NULLABLE_OPERATOR_FIELDS


def test_frontend_sendet_leere_passwoerter_nicht() -> None:
    """Ein leeres Feld heisst 'unveraendert' — sonst loescht jedes
    Speichern die nicht angezeigten Zugangsdaten."""
    quelle = (WURZEL.parent / "frontend" / "src" / "components"
              / "OperatorAdminPanel.svelte").read_text()
    m2 = re.search(r"for \(const k of \[([^\]]+)\]\) \{\s*\n\s*if \(f\[k\]\.trim\(\)\)", quelle)
    assert m2 and "eqsl_password" in m2.group(1)
