"""boot_mode muss beide Schalter merken, nicht nur den zuletzt bedienten.

2026-09-09: ``auto_cq`` (CQ rufen) und ``auto_answer`` (Hunting) sind zwei
unabhaengige Schalter, teilten sich aber das eine Feld ``boot_mode``. Jede
Bedienhandlung schrieb einen festen Wert — CQ-Start "cq", der
Hunting-Schalter "hunt", Stop "off" — und loeschte damit die Erinnerung an
den jeweils anderen. Aufgefallen nach dem Multicore-Update: Die Station kam
in CQ hoch, aber ohne Hunting, also ohne Picker, Tail-Ending und
CQ-Fallback. Wer auf ihr CQ antwortete, wurde weiter bedient; selbst nach
Anrufern suchen tat sie nicht mehr.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from ft8_appliance.config import (
    AntennaConfig,
    AppConfig,
    BandConfig,
    OperatingConfig,
    OperatorConfig,
)
from ft8_appliance.runtime import FakeSlotClock, Orchestrator


def _cfg(boot_mode: str = "off") -> AppConfig:
    return AppConfig(
        operator=OperatorConfig(callsign="DK9XR", default_locator="JN58td"),
        bands=[BandConfig(name="20m", freq_khz=14074, antenna="endfed")],
        antennas=[AntennaConfig(name="endfed", bands=["20m"])],
        operating=OperatingConfig(boot_mode=boot_mode),  # type: ignore[arg-type]
    )


def _orch(cfg: AppConfig) -> Orchestrator:
    async def _no_decodes(_tick):
        return []

    return Orchestrator(
        config=cfg,
        rig=AsyncMock(),
        gps=AsyncMock(),
        decode_source=_no_decodes,
        slot_clock=FakeSlotClock(),
    )


def test_beide_schalter_ergeben_cq_plus_hunt() -> None:
    orch = _orch(_cfg())
    ctx = orch.state_machine.ctx

    ctx.auto_cq = True
    ctx.auto_answer = False
    assert orch._boot_mode_from_state() == "cq"

    ctx.auto_answer = True
    assert orch._boot_mode_from_state() == "cq+hunt"

    ctx.auto_cq = False
    assert orch._boot_mode_from_state() == "hunt"

    ctx.auto_answer = False
    assert orch._boot_mode_from_state() == "off"


async def test_hunting_einschalten_loescht_das_laufende_cq_nicht() -> None:
    """Der Fall vom 2026-09-09: erst CQ, dann Hunting."""
    orch = _orch(_cfg())
    orch.persist_config = AsyncMock()  # type: ignore[method-assign]
    orch.state_machine.ctx.auto_cq = True  # CQ laeuft bereits

    await orch.handle_set_auto_answer(True)

    assert orch.config.operating.boot_mode == "cq+hunt", (
        "Hunting einschalten darf das gemerkte CQ nicht ueberschreiben"
    )


async def test_cq_starten_loescht_das_laufende_hunting_nicht() -> None:
    """Und die Gegenrichtung: erst Hunting, dann CQ."""
    orch = _orch(_cfg())
    orch.persist_config = AsyncMock()  # type: ignore[method-assign]
    orch._refresh_hw_for_control = AsyncMock()  # type: ignore[method-assign]
    orch.state_machine.set_auto_answer(True)
    orch.state_machine.ctx.auto_cq = True  # was on_user_start_cq setzen wuerde

    await orch.handle_start_cq()

    assert orch.config.operating.boot_mode == "cq+hunt"


async def test_stop_schaltet_beides_ab() -> None:
    orch = _orch(_cfg())
    orch.persist_config = AsyncMock()  # type: ignore[method-assign]
    orch.state_machine.ctx.auto_cq = True
    orch.state_machine.ctx.auto_answer = True

    await orch.handle_stop()

    assert orch.config.operating.boot_mode == "off"
    assert orch.state_machine.ctx.auto_cq is False
    assert orch.state_machine.ctx.auto_answer is False


async def test_start_stellt_beide_schalter_wieder_her() -> None:
    """Der Zweck der ganzen Uebung: nach einem Update laeuft beides weiter."""
    orch = _orch(_cfg(boot_mode="cq+hunt"))
    ctx = orch.state_machine.ctx
    assert ctx.auto_cq is False and ctx.auto_answer is False

    # Nur den boot_mode-Zweig aus start() nachstellen — der volle Start
    # braucht Audio, Rig-Poll und Datenbank.
    bm = orch.config.operating.boot_mode
    if bm in ("hunt", "cq+hunt"):
        orch.state_machine.set_auto_answer(True)
    if bm in ("cq", "cq+hunt"):
        ctx.auto_cq = True

    assert ctx.auto_cq is True
    assert ctx.auto_answer is True


def test_start_wertet_beide_werte_aus() -> None:
    """Absicherung gegen ein spaeteres elif: 'cq+hunt' muss in beiden Zweigen stehen."""
    import pathlib

    import ft8_appliance.runtime.orchestrator as _orch_mod

    src = pathlib.Path(_orch_mod.__file__).read_text()
    i = src.index("bm = self.config.operating.boot_mode")
    block = src[i : i + 700]
    assert 'if bm in ("hunt", "cq+hunt")' in block
    assert 'if bm in ("cq", "cq+hunt")' in block
    assert "elif bm ==" not in block, (
        "die beiden Schalter duerfen sich nicht mehr gegenseitig ausschliessen"
    )
