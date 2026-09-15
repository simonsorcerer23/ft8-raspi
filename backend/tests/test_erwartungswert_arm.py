"""Erwartungswert-Arm im Picker und Orchestrator.

Im EW-Arm ersetzt eine Zahl je Kandidat die sieben Lohnt-sich-Gates:
P(Erfolg) x Wert gegen den CQ-Ertrag. Technische Gates bleiben.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from ft8_appliance.analyse.erwartungswert import PTabelle, Wertfaktoren
from ft8_appliance.runtime.orchestrator import Orchestrator
from ft8_appliance.statemachine.machine import StateMachine
from ft8_appliance.statemachine.states import DecodedMsg, MachineContext


def _tabelle() -> PTabelle:
    """EU: stark 30 %, schwach 8 %; NA stark 4 %. Alles mit n=300, damit die
    Schrumpfung die Zellen nicht mehr bewegt."""
    z = []
    z += [(-8, "EU", False, i % 10 < 3) for i in range(300)]
    z += [(-15, "EU", False, i % 100 < 8) for i in range(300)]
    z += [(-8, "NA", False, i % 100 < 4) for i in range(300)]
    return PTabelle.aus_anrufen(z)


def _sm(ew: bool = True) -> StateMachine:
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58td"))
    sm.ctx.auto_answer = True
    sm.ctx.hunt_priority = ["snr"]
    sm.ctx.worked_grids = {"JO31"}
    sm.ctx.worked_grid_band = {("JO31", "20m")}
    sm.ctx.hunt_snr_floor_db = -22
    sm.ctx.hunt_weak_requires_psk = True
    sm.ctx.hunt_weak_snr_db = -13
    sm.ctx.schwach_arm = True
    sm.ctx.call_to_continent = {"DL1AAA": "EU", "DL2BBB": "EU", "K1ABC": "NA"}
    sm.ctx.p_tabelle = _tabelle()
    sm.ctx.ew_faktoren = Wertfaktoren()
    sm.ctx.p_cq = 0.02            # Schwelle = 0.02 * 90 / 30 = 6 %
    sm.ctx.ew_anruf_s = 90.0
    sm.ctx.ew_arm = ew
    return sm


def _cq(call: str, snr: int = -8, dt: float = 0.2) -> DecodedMsg:
    return DecodedMsg(ts=datetime.now(UTC), call_from=call, call_to=None, grid="JO31",
                      message=f"CQ {call} JO31", snr_db=snr, dt_s=dt, freq_offset_hz=1500, band="20m")


def _prot(sm: StateMachine) -> dict[str, dict]:
    return {k["call"]: k for k in sm._last_pick_diag.get("kandidaten") or []}


def test_schwaches_ziel_ueber_der_schwelle_wird_angerufen() -> None:
    """EU schwach ohne Beleg: 8 % > 6 %. Die Kette haette es verworfen."""
    sm = _sm()
    best = sm._pick_hunt_target([_cq("DL1AAA", snr=-15)])
    assert best is not None and best.call_from == "DL1AAA"
    assert sm._last_pick_diag["winning_tier"] == "erwartungswert"


def test_kette_haette_dasselbe_ziel_verworfen() -> None:
    sm = _sm(ew=False)
    assert sm._pick_hunt_target([_cq("DL1AAA", snr=-15)]) is None


def test_ziel_unter_dem_cq_ertrag_wird_verworfen() -> None:
    """NA stark: 4 % < 6 %. Der Slot geht an den CQ-Fallback."""
    sm = _sm()
    assert sm._pick_hunt_target([_cq("K1ABC", snr=-8)]) is None
    assert _prot(sm)["K1ABC"]["verworfen_von"] == "erwartungswert"
    assert sm._last_pick_diag["winning_tier"] == "ew_unter_cq"


def test_wert_hebt_ein_unwahrscheinliches_ziel_ueber_die_schwelle() -> None:
    """NA stark 4 % x neues DXCC 3,0 = 12 % > 6 %."""
    sm = _sm()
    sm.ctx.new_dxcc_calls = {"K1ABC"}
    best = sm._pick_hunt_target([_cq("K1ABC", snr=-8)])
    assert best is not None
    k = _prot(sm)["K1ABC"]
    # 4 % geschrumpft zum Klassenmittel liegt bei knapp 5 %; x3 = 14-15 %.
    assert abs(k["wert"] - 3.0) < 1e-9 and 0.10 < k["ew"] < 0.16


def test_hoechster_ew_gewinnt_nicht_das_staerkste_signal() -> None:
    """EU stark Routine: 30 %. NA stark neues DXCC: ~5 % x 3 = ~15 %.
    Der Routine-Kandidat gewinnt — Wert allein reicht nicht. (Beide in
    Klasse b; -5 dB waere schon Klasse a mit leerer Zelle.)"""
    sm = _sm()
    sm.ctx.new_dxcc_calls = {"K1ABC"}
    best = sm._pick_hunt_target([_cq("DL1AAA", snr=-8), _cq("K1ABC", snr=-7)])
    assert best.call_from == "DL1AAA"


def test_wunschliste_schlaegt_routine() -> None:
    """EU schwach 8 % x Wunschliste 5 = 40 % > EU stark 30 %."""
    sm = _sm()
    sm.ctx.watchlist_calls = {"DL2BBB"}
    best = sm._pick_hunt_target([_cq("DL1AAA", snr=-8), _cq("DL2BBB", snr=-15)])
    assert best.call_from == "DL2BBB"


def test_protokoll_traegt_p_wert_ew() -> None:
    sm = _sm()
    sm._pick_hunt_target([_cq("DL1AAA", snr=-8)])
    k = _prot(sm)["DL1AAA"]
    assert 0.28 < k["p_erfolg"] < 0.32 and k["wert"] == 1.0 and abs(k["ew"] - k["p_erfolg"]) < 1e-9


def test_regelarm_protokolliert_keine_vorhersage() -> None:
    sm = _sm(ew=False)
    sm._pick_hunt_target([_cq("DL1AAA", snr=-8)])
    assert _prot(sm)["DL1AAA"]["p_erfolg"] is None


def test_technische_gates_bleiben_im_ew_arm() -> None:
    sm = _sm()
    assert sm._pick_hunt_target([_cq("DL1AAA", snr=-8, dt=5.0)]) is None
    assert _prot(sm)["DL1AAA"]["verworfen_von"] == "dt_fenster"


def test_ohne_tabelle_stuerzt_nichts() -> None:
    sm = _sm()
    sm.ctx.p_tabelle = None
    sm.ctx.ew_faktoren = None
    assert sm._pick_hunt_target([_cq("DL1AAA", snr=-8)]) is not None


# ---------------------------------------------------------- Orchestrator

def _o(ab: bool = True, kontrolle: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(operating=SimpleNamespace(hunt_erwartungswert_ab=ab)),
        state_machine=SimpleNamespace(ctx=SimpleNamespace(kontroll_arm=kontrolle, ew_arm=False)),
        _ew_arm_slot=-1, _ew_arm=False, _FERN_ARM_BLOCK_S=900.0,
    )


def test_ew_arm_folgt_dem_block() -> None:
    import ft8_appliance.runtime.orchestrator as mod
    werte = set()
    for block in range(40):
        o = _o()
        mod.time = SimpleNamespace(time=lambda b=block: b * 900.0 + 1.0, monotonic=mod.time.monotonic)
        try:
            Orchestrator._setze_ew_arm(o)
        finally:
            import time as _t
            mod.time = _t
        werte.add(o.state_machine.ctx.ew_arm)
    assert werte == {True, False}


def test_kontrolle_hat_vorrang() -> None:
    o = _o(kontrolle=True)
    Orchestrator._setze_ew_arm(o)
    assert o.state_machine.ctx.ew_arm is False


def test_ab_aus_heisst_kette() -> None:
    o = _o(ab=False)
    Orchestrator._setze_ew_arm(o)
    assert o.state_machine.ctx.ew_arm is False


def test_eigenes_salz_und_beide_aufrufstellen() -> None:
    quelle = (Path(__file__).resolve().parents[1] / "ft8_appliance" / "runtime" / "orchestrator.py").read_text()
    assert '_arm_aus_block(block, "erwartungswert")' in quelle
    assert len(re.findall(r"^\s+self\._setze_ew_arm\(\)$", quelle, re.M)) == 2
    assert quelle.count('ew_arm=meta.get("ew_arm")') == 1


def test_modell_bleibt_abgeschaltet() -> None:
    """Am 15.09.2026 abgeschaltet, nachdem es zwei Tage lang verlor:
    1,82 QSOs je Stunde gegen 2,66 im Regelarm, und auch wertgewichtet
    hinten (2,28 gegen 3,16). Wer den Default wieder umlegt, schaltet
    einen gemessenen Verlust scharf — dann bitte mit neuen Zahlen und
    deutlich kleineren Wertfaktoren.
    """
    from ft8_appliance.config.models import OperatingConfig
    assert OperatingConfig().hunt_erwartungswert_ab is False
