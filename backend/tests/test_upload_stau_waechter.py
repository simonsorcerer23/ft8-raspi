"""Waechter auf das Ergebnis statt auf Abstuerze.

Der bestehende Drain-Waechter zaehlt geworfene Schleifen. Am 2026-09-12
hingen zwei QSOs dreizehn Stunden bei ClubLog fest, ohne dass irgendetwas
warf — er konnte den Fall per Konstruktion nicht sehen.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace

import pytest

from ft8_appliance.db import session_scope
from ft8_appliance.db.models import Qso
from ft8_appliance.db.session import create_all, init_engine
from ft8_appliance.runtime.orchestrator import Orchestrator


class _Ntfy:
    enabled = True

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def notify(self, msg: str, **kw) -> None:
        self.sent.append({"msg": msg, **kw})


def _operator(callsign: str, *, qrz: bool = True, clublog: bool = True):
    return SimpleNamespace(
        callsign=callsign,
        qrz_logbook_api_key="KEY" if qrz else None,
        qrz_key_for=lambda _s, _q=qrz: ("KEY" if _q else None),
        clublog_email=f"{callsign.lower()}@example.com" if clublog else None,
        clublog_app_password="pw" if clublog else None,
        clublog_api_key="clkey" if clublog else None,
    )


def _stub(ops: list, ntfy: _Ntfy | None = None) -> SimpleNamespace:
    stub = SimpleNamespace(
        config=SimpleNamespace(demo_mode=False, operators=ops, operator=ops[0]),
        integrations=SimpleNamespace(ntfy=ntfy),
        _UPLOAD_STAU_STUNDEN=Orchestrator._UPLOAD_STAU_STUNDEN,
        _UPLOAD_STAU_MELDE_ABSTAND_S=Orchestrator._UPLOAD_STAU_MELDE_ABSTAND_S,
        _upload_stau_gemeldet_at=0.0,
        _maybe_persist_runtime_state=lambda *a, **kw: None,
    )
    stub._operator_for_call = partial(Orchestrator._operator_for_call, stub)
    stub._operator_for_qso = partial(Orchestrator._operator_for_qso, stub)
    stub._pruefe_upload_stau = partial(Orchestrator._pruefe_upload_stau, stub)
    return stub


async def _seed(eintraege: list[dict]) -> None:
    async with session_scope() as s:
        for i, extra in enumerate(eintraege):
            alter_h = extra.pop("alter_h", 0.0)
            start = datetime.now(UTC) - timedelta(hours=alter_h)
            s.add(Qso(
                call=extra.pop("call", f"W1AW{i}"),
                band="20m", freq_hz=14_074_000, mode="FT8",
                rst_sent=-12, rst_rcvd=-7,
                qso_start=start, qso_end=start, my_grid="JN58ch",
                **extra,
            ))


@pytest.mark.asyncio
async def test_meldet_liegengebliebenes_qso(tmp_path) -> None:
    """Genau der Fall vom 2026-09-12: der Upload wirft nicht, er kommt
    nur nicht an."""
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DK9XR")
    await _seed([{"call": "RA3GZ", "user_callsign": "DK9XR", "alter_h": 13.0,
                  "qrz_uploaded": True, "clublog_uploaded": False,
                  "clublog_upload_attempts": 12}])
    ntfy = _Ntfy()
    stub = _stub([_operator("DK9XR")], ntfy)
    await stub._pruefe_upload_stau()
    assert len(ntfy.sent) == 1
    assert "RA3GZ" in ntfy.sent[0]["msg"]
    assert "ClubLog" in ntfy.sent[0]["msg"]


@pytest.mark.asyncio
async def test_frisches_qso_ist_kein_stau(tmp_path) -> None:
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DK9XR")
    await _seed([{"call": "K1NEW", "user_callsign": "DK9XR", "alter_h": 0.2,
                  "qrz_uploaded": False, "clublog_uploaded": False}])
    ntfy = _Ntfy()
    await _stub([_operator("DK9XR")], ntfy)._pruefe_upload_stau()
    assert ntfy.sent == []


@pytest.mark.asyncio
async def test_verwaistes_qso_meldet_nicht(tmp_path) -> None:
    """Das QSO eines geloeschten Profils liegt absichtlich — es hat
    niemanden, der es hochladen koennte."""
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DK9XR")
    await _seed([{"call": "K1ORPHAN", "user_callsign": "DL1GONE",
                  "alter_h": 48.0, "qrz_uploaded": False,
                  "clublog_uploaded": False}])
    ntfy = _Ntfy()
    await _stub([_operator("DK9XR")], ntfy)._pruefe_upload_stau()
    assert ntfy.sent == []


@pytest.mark.asyncio
async def test_ohne_zugangsdaten_kein_alarm(tmp_path) -> None:
    """Wer ClubLog nicht eingerichtet hat, will darueber nicht gewarnt
    werden."""
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DK9XR")
    await _seed([{"call": "K1NOCL", "user_callsign": "DK9XR", "alter_h": 24.0,
                  "qrz_uploaded": True, "clublog_uploaded": False}])
    ntfy = _Ntfy()
    await _stub([_operator("DK9XR", clublog=False)], ntfy)._pruefe_upload_stau()
    assert ntfy.sent == []


@pytest.mark.asyncio
async def test_meldet_hoechstens_einmal_am_tag(tmp_path) -> None:
    """Der Stau loest sich nicht in Minuten — und das Gedaechtnis dafuer
    muss den Neustart ueberleben, sonst meldet er bei einem Median von
    zwanzig Minuten Laufzeit faktisch bei jedem Start."""
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DK9XR")
    await _seed([{"call": "RU3ACA", "user_callsign": "DK9XR", "alter_h": 13.0,
                  "qrz_uploaded": True, "clublog_uploaded": False}])
    ntfy = _Ntfy()
    stub = _stub([_operator("DK9XR")], ntfy)
    await stub._pruefe_upload_stau()
    assert len(ntfy.sent) == 1
    # Zweiter Durchgang direkt danach: derselbe Stau, kein zweiter Push.
    await stub._pruefe_upload_stau()
    assert len(ntfy.sent) == 1
    # Ein frisch gestarteter Prozess laedt den Zeitpunkt aus runtime_state.
    neuer_prozess = _stub([_operator("DK9XR")], ntfy)
    neuer_prozess._upload_stau_gemeldet_at = stub._upload_stau_gemeldet_at
    await neuer_prozess._pruefe_upload_stau()
    assert len(ntfy.sent) == 1


@pytest.mark.asyncio
async def test_nach_einem_tag_meldet_er_wieder(tmp_path) -> None:
    init_engine(tmp_path / "qso.sqlite")
    await create_all(default_user_callsign="DK9XR")
    await _seed([{"call": "RU3ACA", "user_callsign": "DK9XR", "alter_h": 30.0,
                  "qrz_uploaded": True, "clublog_uploaded": False}])
    ntfy = _Ntfy()
    stub = _stub([_operator("DK9XR")], ntfy)
    stub._upload_stau_gemeldet_at = time.time() - 86400.0 - 60.0
    await stub._pruefe_upload_stau()
    assert len(ntfy.sent) == 1
