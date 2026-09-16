"""Permanenter Kontrollarm: ~10 % der Zeitbloecke ohne Lohnt-sich-Gates.

Seit 2026-09-14. Eine Regel, die wirkt, verhindert ihre eigene
Ueberpruefung: Die Verworfenen werden nie angerufen, also gibt es keine
Daten, die die Regel widerlegen koennten. Der Kontrollarm ist der
dauerhafte Massstab — QSOs je Stunde Regel gegen Kontrolle.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from ft8_appliance.runtime.orchestrator import Orchestrator, _arm_aus_block, _kontroll_block
from ft8_appliance.statemachine.machine import StateMachine
from ft8_appliance.statemachine.states import DecodedMsg, MachineContext


def _sm() -> StateMachine:
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58td"))
    sm.ctx.auto_answer = True
    sm.ctx.hunt_priority = ["snr"]
    sm.ctx.worked_grids = {"JO31"}
    sm.ctx.worked_grid_band = {("JO31", "20m")}
    sm.ctx.hunt_snr_floor_db = -22
    sm.ctx.hunt_sole_min_snr_db = -16
    sm.ctx.hunt_weak_requires_psk = True
    sm.ctx.hunt_weak_snr_db = -13
    sm.ctx.schwach_arm = True            # starrer Arm: Filter greift immer
    return sm


def _cq(call: str, snr: int = -8, dt: float = 0.2) -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call, call_to=None, grid="JO31",
        message=f"CQ {call} JO31", snr_db=snr, dt_s=dt,
        freq_offset_hz=1500, band="20m",
    )


def _verworfen(sm: StateMachine) -> dict[str, str | None]:
    return {k["call"]: k["verworfen_von"] for k in sm._last_pick_diag.get("kandidaten") or []}


def _haette(sm: StateMachine) -> dict[str, str | None]:
    """Was HAETTE im Kontrollarm gegriffen — das Schattenprotokoll."""
    return {k["call"]: k["haette_verworfen"]
            for k in sm._last_pick_diag.get("kandidaten") or []}


# ------------------------------------------------------------ Regelarm

def test_regelarm_verwirft_schwaches_ziel_ohne_beleg() -> None:
    sm = _sm()
    assert sm._pick_hunt_target([_cq("DL1AAA", snr=-15)]) is None
    assert _verworfen(sm)["DL1AAA"] == "schwach_ohne_psk"


def test_regelarm_verwirft_unter_dem_floor() -> None:
    sm = _sm()
    assert sm._pick_hunt_target([_cq("DL1AAA", snr=-24)]) is None
    assert _verworfen(sm)["DL1AAA"] == "snr_floor"


# --------------------------------------------------------- Kontrollarm

def test_kontrollarm_ruft_schwaches_ziel_ohne_beleg_an() -> None:
    sm = _sm()
    sm.ctx.kontroll_arm = True
    best = sm._pick_hunt_target([_cq("DL1AAA", snr=-15)])
    assert best is not None and best.call_from == "DL1AAA"


def test_kontrollarm_ruft_unter_dem_floor_an() -> None:
    """Der Floor ist eine Chancen-Schaetzung, kein technischer Grund."""
    sm = _sm()
    sm.ctx.kontroll_arm = True
    best = sm._pick_hunt_target([_cq("DL1AAA", snr=-24)])
    assert best is not None


def test_kontrollarm_laesst_einzelkandidat_durch() -> None:
    sm = _sm()
    sm.ctx.hunt_weak_requires_psk = False
    sm.ctx.kontroll_arm = True
    assert sm._pick_hunt_target([_cq("DL1AAA", snr=-19)]) is not None


def test_kontrollarm_ignoriert_strict_modus() -> None:
    sm = _sm()
    sm.ctx.hunt_weak_requires_psk = False
    sm.ctx.hunt_strict_until = datetime.now(UTC).timestamp() + 600
    sm.ctx.kontroll_arm = True
    assert sm._pick_hunt_target([_cq("DL1AAA", snr=-15), _cq("DL2BBB", snr=-15)]) is not None


def test_kontrollarm_ignoriert_kontinent_gate() -> None:
    sm = _sm()
    sm.ctx.hunt_continent_gate = True
    sm.ctx.hunt_continent_gate_pct = 5
    sm.ctx.continent_success = {"NA": 0.01}
    sm.ctx.call_to_continent = {"K1ABC": "NA"}
    sm.ctx.kontroll_arm = True
    assert sm._pick_hunt_target([_cq("K1ABC", snr=-5)]) is not None


def test_kontrollarm_ignoriert_pile_up() -> None:
    sm = _sm()
    sm.ctx.pile_up_calls = {"DL1AAA"}
    sm.ctx.kontroll_arm = True
    assert sm._pick_hunt_target([_cq("DL1AAA", snr=-5)]) is not None


# ------------------------------- Schattenprotokoll (v0.162.0)

# Der Kontrollarm ruft an, WEIL ein Gate ihn sonst gehindert haette.
# Welches Gate das war, muss mitgeschrieben werden: Ohne diese Spalte
# liesse sich je Stufe nur rekonstruieren, wen sie getroffen haette —
# und eine Rekonstruktion, die die Filterlogik nachbaut, misst am Ende
# den Nachbau. Angewandt wird im Kontrollarm weiterhin nichts.

def test_schatten_nennt_den_floor() -> None:
    sm = _sm()
    sm.ctx.kontroll_arm = True
    best = sm._pick_hunt_target([_cq("DL1AAA", snr=-24)])
    assert best is not None
    assert _haette(sm)["DL1AAA"] == "snr_floor"
    assert _verworfen(sm)["DL1AAA"] is None      # angewandt wurde nichts


def test_schatten_nennt_das_schwach_gate() -> None:
    sm = _sm()
    sm.ctx.kontroll_arm = True
    assert sm._pick_hunt_target([_cq("DL1AAA", snr=-15)]) is not None
    assert _haette(sm)["DL1AAA"] == "schwach_ohne_psk"


def test_schatten_nennt_das_kontinent_gate() -> None:
    sm = _sm()
    sm.ctx.hunt_continent_gate = True
    sm.ctx.hunt_continent_gate_pct = 5
    sm.ctx.continent_success = {"NA": 0.01}
    sm.ctx.call_to_continent = {"K1ABC": "NA"}
    sm.ctx.kontroll_arm = True
    assert sm._pick_hunt_target([_cq("K1ABC", snr=-5)]) is not None
    assert _haette(sm)["K1ABC"] == "kontinent_gate"


def test_schatten_nennt_pile_up() -> None:
    sm = _sm()
    sm.ctx.pile_up_calls = {"DL1AAA"}
    sm.ctx.kontroll_arm = True
    assert sm._pick_hunt_target([_cq("DL1AAA", snr=-5)]) is not None
    assert _haette(sm)["DL1AAA"] == "pile_up"


def test_schatten_nennt_strict_modus() -> None:
    sm = _sm()
    sm.ctx.hunt_weak_requires_psk = False
    sm.ctx.hunt_strict_until = datetime.now(UTC).timestamp() + 600
    sm.ctx.kontroll_arm = True
    assert sm._pick_hunt_target([_cq("DL1AAA", snr=-15), _cq("DL2BBB", snr=-15)]) is not None
    assert _haette(sm)["DL1AAA"] == "strict_modus"


def test_schatten_nennt_den_einzelnen_schwachen_cq() -> None:
    """Dieses Gate wirkt durch Abbruch des Slots, nicht durch Kuerzen
    der Liste — im Kontrollarm darf es trotzdem nicht abbrechen."""
    sm = _sm()
    sm.ctx.hunt_weak_requires_psk = False
    sm.ctx.kontroll_arm = True
    best = sm._pick_hunt_target([_cq("DL1AAA", snr=-19)])
    assert best is not None
    assert _haette(sm)["DL1AAA"] == "einzelner_schwacher_cq"


def test_erste_stufe_gewinnt_im_schatten() -> None:
    """Wie bei verworfen_von: der Grund ist der ERSTE, der gegriffen
    haette. Sonst haengt die Zuordnung an der Reihenfolge der Kette."""
    sm = _sm()
    sm.ctx.pile_up_calls = {"DL1AAA"}
    sm.ctx.kontroll_arm = True
    assert sm._pick_hunt_target([_cq("DL1AAA", snr=-24)]) is not None
    assert _haette(sm)["DL1AAA"] == "snr_floor"


def test_regelarm_schreibt_kein_schattenprotokoll() -> None:
    """Dort wird angewandt — verworfen_von traegt den Grund, und eine
    zweite Spalte mit demselben Inhalt waere nur eine Fehlerquelle."""
    sm = _sm()
    assert sm._pick_hunt_target([_cq("DL1AAA", snr=-24)]) is None
    assert _verworfen(sm)["DL1AAA"] == "snr_floor"
    assert _haette(sm)["DL1AAA"] is None


def test_durchgelassene_tragen_nichts() -> None:
    sm = _sm()
    sm.ctx.kontroll_arm = True
    assert sm._pick_hunt_target([_cq("DL1AAA", snr=-5)]) is not None
    assert _haette(sm)["DL1AAA"] is None


# ------------------------------------------- technische Gates bleiben

def test_technische_gates_gelten_auch_in_der_kontrolle() -> None:
    """DT ausserhalb des Fensters: die Station hoert uns nicht. Das ist
    kein Lohnt-sich-Urteil, das bleibt in beiden Armen."""
    sm = _sm()
    sm.ctx.kontroll_arm = True
    assert sm._pick_hunt_target([_cq("DL1AAA", snr=-5, dt=5.0)]) is None
    assert _verworfen(sm)["DL1AAA"] == "dt_fenster"


def test_cooldown_gilt_auch_in_der_kontrolle() -> None:
    sm = _sm()
    sm.ctx.kontroll_arm = True
    sm.ctx.recent_until["DL1AAA"] = datetime.now(UTC).timestamp() + 600
    assert sm._pick_hunt_target([_cq("DL1AAA", snr=-5)]) is None
    assert _verworfen(sm)["DL1AAA"] == "cooldown"


# ------------------------------------------------------ Blockzuteilung

def test_anteil_stimmt_ungefaehr() -> None:
    n = sum(_kontroll_block(b, 0.1) for b in range(20000))
    assert 1700 < n < 2300, n


def test_anteil_null_schaltet_ab() -> None:
    assert not any(_kontroll_block(b, 0.0) for b in range(2000))


def test_deterministisch() -> None:
    assert [_kontroll_block(b, 0.1) for b in range(500)] == [_kontroll_block(b, 0.1) for b in range(500)]


def test_kontrolle_hat_ein_eigenes_salz() -> None:
    """Gleiches Salz wie ein A/B-Test hiesse: derselbe Hash entscheidet
    beide. Heute nehmen die Funktionen verschiedene Bits daraus, das ist
    Zufall, kein Schutz. Deshalb steht das Salz im Quelltext fest."""
    quelle = (Path(__file__).resolve().parents[1]
              / "ft8_appliance" / "runtime" / "orchestrator.py").read_text()
    assert 'hashlib.sha256(("kontrolle" + str(block))' in quelle
    ab_salze = re.findall(r'_arm_aus_block\(block(?:,\s*"([^"]*)")?\)', quelle)
    assert "kontrolle" not in ab_salze


def test_orchestrator_setzt_den_arm_aus_der_config() -> None:
    o = SimpleNamespace(
        config=SimpleNamespace(operating=SimpleNamespace(hunt_kontrollarm_anteil=1.0)),
        state_machine=SimpleNamespace(ctx=SimpleNamespace(kontroll_arm=False)),
        _kontroll_arm_slot=-1, _kontroll_arm=False,
        _FERN_ARM_BLOCK_S=900.0,
    )
    Orchestrator._setze_kontroll_arm(o)
    assert o.state_machine.ctx.kontroll_arm is True
    o.config.operating.hunt_kontrollarm_anteil = 0.0
    o._kontroll_arm_slot = -1
    Orchestrator._setze_kontroll_arm(o)
    assert o.state_machine.ctx.kontroll_arm is False


def test_kontrollarm_wird_je_tick_gesetzt() -> None:
    """Beide Aufrufstellen der Arm-Setzer muessen ihn setzen, sonst
    haengt ein Pfad (Vorab-Durchgang) im Regelarm fest."""
    quelle = (Path(__file__).resolve().parents[1]
              / "ft8_appliance" / "runtime" / "orchestrator.py").read_text()
    assert len(re.findall(r"^\s+self\._setze_schwach_arm\(\)$", quelle, re.M)) == \
           len(re.findall(r"^\s+self\._setze_kontroll_arm\(\)$", quelle, re.M)) == 2


def test_arm_steht_im_anrufdatensatz() -> None:
    """Der Arm muss an beiden Stellen, die die Anruf-Telemetrie
    befuellen (Pick und eingehender Anruf), neben schwach_arm stehen,
    und der Orchestrator muss ihn in die Zeile schreiben. Fehlt eine
    Stelle, hat die Bilanz fuer diesen Pfad keinen Arm — und der
    Vergleich QSOs je Arm ist still verzerrt."""
    wurzel = Path(__file__).resolve().parents[1] / "ft8_appliance"
    maschine = (wurzel / "statemachine" / "machine.py").read_text()
    paare = re.findall(
        r'"schwach_arm": bool\(self\.ctx\.schwach_arm\),\n\s*"kontroll_arm": bool\(self\.ctx\.kontroll_arm\),',
        maschine,
    )
    assert len(paare) == maschine.count('"schwach_arm": bool(self.ctx.schwach_arm)') == 2
    orch = (wurzel / "runtime" / "orchestrator.py").read_text()
    assert orch.count('kontroll_arm=meta.get("kontroll_arm")') == 1
