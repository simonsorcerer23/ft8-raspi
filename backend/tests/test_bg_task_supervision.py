"""Audit 2026-07-30: sterbende Background-Loops waren still.

20 Subsysteme laufen als asyncio.Task — Slot-Clock, Rig-Poll, beide
Upload-Drains, Wartung, Watchdogs. Stirbt einer an einer Exception, liegt
die Exception im Task-Objekt und taucht bestenfalls beim Garbage-Collect
als "Task exception was never retrieved" auf. Die Box laeuft weiter,
nur ohne diese Funktion.

Das ist kein theoretisches Risiko: ein tz-Bug in ``_as_utc`` hat beide
Upload-Drains rund zwei Wochen lang totgelegt: bemerkt wurde es erst
beim Nachzaehlen der QSOs.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from ft8_appliance.runtime.orchestrator import Orchestrator


class _Ntfy:
    enabled = True

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def notify(self, msg: str, **kw) -> None:
        self.sent.append({"msg": msg, **kw})


def _stub(*, ntfy: _Ntfy | None = None) -> SimpleNamespace:
    stub = SimpleNamespace(
        _bg_tasks=[],
        integrations=SimpleNamespace(ntfy=ntfy),
    )
    stub._spawn = lambda coro, *, name: Orchestrator._spawn(stub, coro, name=name)
    stub._on_bg_task_done = lambda t: Orchestrator._on_bg_task_done(stub, t)
    stub._alert_bg_task_death = lambda n, e: Orchestrator._alert_bg_task_death(stub, n, e)
    return stub


async def _drain() -> None:
    """Dem Event-Loop Gelegenheit geben, done-Callbacks abzuarbeiten."""
    for _ in range(3):
        await asyncio.sleep(0)


class _Records(logging.Handler):
    """Log-Mitschnitt direkt am Logger — bewusst NICHT ueber caplog.

    ``web/app.py`` setzt ``propagate = False`` auf dem ft8_appliance-Logger
    (sonst landet alles doppelt via root). caplog haengt aber am
    Root-Logger: sobald irgendein anderer Test die App baut, sieht caplog
    hier nichts mehr und die Tests werden abhaengig von der Reihenfolge.
    """

    def __init__(self, *names: str) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []
        self._names = names or ("ft8_appliance.runtime.orchestrator",)

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def messages(self, level: int) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno >= level]

    def __enter__(self) -> _Records:
        self._loggers = [logging.getLogger(n) for n in self._names]
        self._prev = [lg.level for lg in self._loggers]
        for lg in self._loggers:
            lg.setLevel(logging.DEBUG)
            lg.addHandler(self)
        return self

    def __exit__(self, *exc) -> None:
        for lg, lvl in zip(self._loggers, self._prev, strict=True):
            lg.removeHandler(self)
            lg.setLevel(lvl)


# --------------------------------------------------------------- Grundverhalten


@pytest.mark.asyncio
async def test_spawn_registers_the_task_for_shutdown() -> None:
    """_spawn ersetzt _bg_tasks.append() — wer da nicht drinsteht, wird
    beim stop() nicht gecancelt und haengt den Shutdown auf."""
    stub = _stub()

    async def loop() -> None:
        await asyncio.sleep(0)

    task = stub._spawn(loop(), name="test-loop")
    assert stub._bg_tasks == [task]
    assert task.get_name() == "test-loop"
    await task


@pytest.mark.asyncio
async def test_a_dying_loop_is_logged_as_an_error() -> None:
    """Der eigentliche Befund: vorher stand hier gar nichts im Log."""
    stub = _stub()

    async def boom() -> None:
        raise RuntimeError("tz-Bug")

    with _Records() as rec:
        stub._spawn(boom(), name="clublog-drain")
        await _drain()

    errors = rec.messages(logging.ERROR)
    assert len(errors) == 1
    assert "clublog-drain" in errors[0]
    assert "tz-Bug" in errors[0]


@pytest.mark.asyncio
async def test_a_dying_loop_pushes_ntfy() -> None:
    """Ein Log-Eintrag auf dem Pi faellt niemandem auf. Ein toter Loop
    repariert sich nicht selbst, also muss Sebastian es aufs Handy
    bekommen."""
    ntfy = _Ntfy()
    stub = _stub(ntfy=ntfy)

    async def boom() -> None:
        raise RuntimeError("tz-Bug")

    stub._spawn(boom(), name="qrz-logbook-drain")
    await _drain()

    assert len(ntfy.sent) == 1
    push = ntfy.sent[0]
    assert "qrz-logbook-drain" in push["title"]
    assert "tz-Bug" in push["msg"]
    assert push["priority"] == "high"


# ------------------------------------------------------------ kein Fehlalarm


@pytest.mark.asyncio
async def test_cancelled_tasks_are_silent() -> None:
    """stop() cancelt alle Loops. Wuerde das als Absturz gelten, feuerte
    jeder Neustart des Dienstes 20 Push-Nachrichten.

    Auf das Log-Assert kommt es hier an: CancelledError ist BaseException
    und wird von einem blanken ``except Exception`` nicht gefangen. Ohne
    die Pruefung faellt der Fall durch — der Callback wirft dann still in
    den Loop-Exception-Handler, statt einen Push zu schicken.
    """
    ntfy = _Ntfy()
    stub = _stub(ntfy=ntfy)

    async def forever() -> None:
        await asyncio.sleep(3600)

    # "asyncio" mitschneiden: eine aus dem Callback entkommende
    # CancelledError landet im Loop-Exception-Handler, der genau dort
    # loggt — sonst saehe der Test den Fehlerfall gar nicht.
    with _Records("ft8_appliance.runtime.orchestrator", "asyncio") as rec:
        task = stub._spawn(forever(), name="slot-loop")
        await _drain()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _drain()

    assert ntfy.sent == []
    assert rec.messages(logging.ERROR) == []


@pytest.mark.asyncio
async def test_a_clean_early_return_is_info_not_error() -> None:
    """Mehrere Loops kehren bewusst frueh zurueck (z.B. demo_mode). Das
    ist kein Absturz — aber sichtbar sein soll es trotzdem, sonst laesst
    sich "Loop tot" und "Loop nie gelaufen" nicht unterscheiden."""
    ntfy = _Ntfy()
    stub = _stub(ntfy=ntfy)

    async def early_return() -> None:
        return

    with _Records() as rec:
        stub._spawn(early_return(), name="psk-reciprocity-refresh")
        await _drain()

    assert rec.messages(logging.ERROR) == []
    assert ntfy.sent == []
    assert any("psk-reciprocity-refresh" in m for m in rec.messages(logging.INFO))


@pytest.mark.asyncio
async def test_death_report_survives_a_broken_ntfy() -> None:
    """Der Callback laeuft synchron im Event-Loop. Wuerde er werfen,
    landete das im Loop-Exception-Handler — also wieder genau das stille
    Verschlucken, das hier abgestellt werden soll."""
    stub = _stub(ntfy=SimpleNamespace(enabled=True, notify=None))

    async def boom() -> None:
        raise RuntimeError("tz-Bug")

    with _Records() as rec:
        stub._spawn(boom(), name="rig-poll")
        await _drain()

    assert any("rig-poll" in m for m in rec.messages(logging.ERROR))


@pytest.mark.asyncio
async def test_no_ntfy_configured_is_not_an_error() -> None:
    stub = _stub(ntfy=None)

    async def boom() -> None:
        raise RuntimeError("tz-Bug")

    stub._spawn(boom(), name="db-maintenance")
    await _drain()  # darf nicht werfen


# ------------------------------------------------------- Verdrahtung in start()


def test_every_background_loop_goes_through_spawn() -> None:
    """Sonst haelt der Schutz nur bis zum naechsten neuen Loop. Wer
    _bg_tasks.append(create_task(...)) schreibt, hat wieder einen stillen
    Task — und genau das faellt im Betrieb nicht auf.
    """
    from pathlib import Path

    import ft8_appliance.runtime.orchestrator as orch_mod

    src = Path(orch_mod.__file__).read_text()
    assert "self._bg_tasks.append(asyncio.create_task(" not in src
    # 20 Subsysteme laut Audit — bricht bewusst, wenn jemand einen Loop
    # hinzufuegt, damit die Zahl hier bewusst mitgezogen wird.
    assert src.count("self._spawn(") >= 20
