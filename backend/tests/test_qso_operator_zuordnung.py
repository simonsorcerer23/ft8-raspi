"""Ein QSO gehoert dem Operator, unter dessen Rufzeichen es gesendet wurde.

18.11.2026: Das Log-Ende eines QSOs als DK9XR kam zehn Sekunden nach dem
Wechsel auf DO3XR an und wurde DO3XR zugeschrieben. DO3XR hat kein Logbuch
fuer DK9XR, die Uploads zu QRZ und Club Log blieben liegen.
"""
from __future__ import annotations

from types import SimpleNamespace

from ft8_appliance.runtime.orchestrator import Orchestrator


def _orch(aktiv: str):
    ops = [SimpleNamespace(callsign="DK9XR"), SimpleNamespace(callsign="DO3XR")]
    cfg = SimpleNamespace(operators=ops,
                          operator=next(o for o in ops if o.callsign == aktiv))
    return SimpleNamespace(config=cfg)


def test_gesendetes_rufzeichen_entscheidet_nicht_der_aktive_operator():
    o = _orch("DO3XR")
    assert Orchestrator._operator_fuer_station(o, "DK9XR") == "DK9XR"


def test_varianten_gehoeren_zum_heimatruf():
    o = _orch("DO3XR")
    assert Orchestrator._operator_fuer_station(o, "DK9XR/MM") == "DK9XR"
    assert Orchestrator._operator_fuer_station(o, "EA8/DK9XR") == "DK9XR"


def test_ohne_oder_fremdes_rufzeichen_gilt_der_aktive_operator():
    o = _orch("DO3XR")
    assert Orchestrator._operator_fuer_station(o, None) == "DO3XR"
    assert Orchestrator._operator_fuer_station(o, "") == "DO3XR"
    assert Orchestrator._operator_fuer_station(o, "DL0MF") == "DO3XR"
