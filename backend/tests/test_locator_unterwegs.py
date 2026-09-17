"""Welcher Locator gilt — daheim und unterwegs?

Bis v0.167 gewann immer der eingestellte Heimat-Locator; das GPS wurde nur
gefragt, wenn keiner eingestellt war. Bei DK9XR steht JN58 fest drin. Als
DK9XR/MM auf See waere damit JN58 geloggt und im CQ gesendet worden.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ft8_appliance.config import OperatorConfig
from ft8_appliance.runtime.orchestrator import Orchestrator


def _o(op: OperatorConfig, gps: str | None) -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(operator=op), _gps_locator=gps)


def test_daheim_gilt_der_eingestellte_locator() -> None:
    op = OperatorConfig(callsign="DK9XR", default_locator="JN58bh")
    assert Orchestrator.aktueller_locator(_o(op, "JN58bg")) == "JN58bh"


def test_daheim_ohne_eingestellten_nimmt_er_das_gps() -> None:
    op = OperatorConfig(callsign="DK9XR")
    assert Orchestrator.aktueller_locator(_o(op, "JN58bg")) == "JN58bg"


def test_auf_see_zaehlt_das_gps() -> None:
    op = OperatorConfig(callsign="DK9XR", default_locator="JN58bh",
                        current_operating_suffix="MM")
    assert Orchestrator.aktueller_locator(_o(op, "IN91ab")) == "IN91ab"


def test_auf_see_ohne_fix_lieber_keiner_als_der_von_daheim() -> None:
    op = OperatorConfig(callsign="DK9XR", default_locator="JN58bh",
                        current_operating_suffix="MM")
    assert Orchestrator.aktueller_locator(_o(op, None)) is None


def test_log_und_entfernung_fragen_dieselbe_regel() -> None:
    """Drei Stellen lasen den Locator bisher selbst — und alle drei zogen
    den Heimat-Locator vor. Keine darf das wieder eigenmaechtig tun."""
    quelle = (Path(__file__).resolve().parents[1] / "ft8_appliance" / "runtime"
              / "orchestrator.py").read_text()
    code = [z for z in quelle.splitlines() if not z.strip().startswith("#")]
    treffer = [z.strip() for z in code if "default_locator or self.state_machine.ctx.my_grid" in z]
    assert treffer == [], treffer
