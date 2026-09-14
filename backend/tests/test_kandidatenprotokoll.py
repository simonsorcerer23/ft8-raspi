"""Kandidatenprotokoll: jeder Kandidat eines Slots mit seinem Schicksal.

Seit 2026-09-14. Vorher stand ueber die Verworfenen nur ein Zaehler je
Filterstufe. Wer das war, wie stark, aus welchem Land: nirgends. Eine
Regel, die wirkt, verhindert so ihre eigene Ueberpruefung — genau das
ist mit dem Schwach-Filter, dem Kontinent-Gate und der Stundenregel
passiert. Dieses Protokoll ist die Datengrundlage fuer den dauerhaften
Kontrollarm.
"""
from __future__ import annotations

from datetime import UTC, datetime

from ft8_appliance.statemachine.machine import StateMachine
from ft8_appliance.statemachine.states import DecodedMsg, MachineContext


def _sm() -> StateMachine:
    """Kontext wie im Betrieb: Award-Mengen gefuellt.

    In einem leeren Kontext gilt jedes Grid als neu und damit jedes Ziel
    als Award-Pick — der Einzelkandidaten-Filter koennte dann nie greifen,
    und der Test misst nur sein eigenes Artefakt.
    """
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58td"))
    sm.ctx.auto_answer = True
    sm.ctx.hunt_priority = ["snr"]
    sm.ctx.worked_grids = {"JO31"}
    sm.ctx.worked_grid_band = {("JO31", "20m")}
    return sm


def _cq(call: str, snr: int = -8, dt: float = 0.2, grid: str | None = "JO31") -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call, call_to=None, grid=grid,
        message=f"CQ {call} {grid or ''}".strip(), snr_db=snr, dt_s=dt,
        freq_offset_hz=1500, band="20m",
    )


def _protokoll(sm: StateMachine) -> list[dict]:
    return sm._last_pick_diag.get("kandidaten") or []


# ---------------------------------------------------------------- Grundmenge

def test_jeder_kandidat_erscheint_genau_einmal() -> None:
    sm = _sm()
    sm._pick_hunt_target([_cq("DL1AAA"), _cq("DL2BBB", dt=5.0), _cq("DL3CCC", snr=-25)])
    calls = sorted(k["call"] for k in _protokoll(sm))
    assert calls == ["DL1AAA", "DL2BBB", "DL3CCC"]


def test_verworfener_traegt_den_filternamen() -> None:
    """Der Zaehler allein sagte nur 'plus eins'. Jetzt steht dabei, wer."""
    sm = _sm()
    sm._pick_hunt_target([_cq("DL1AAA"), _cq("DL2BBB", dt=5.0)])
    p = {k["call"]: k for k in _protokoll(sm)}
    assert p["DL2BBB"]["verworfen_von"] == "dt_fenster"
    assert p["DL2BBB"]["gewaehlt"] is False


def test_gewinner_ist_markiert_und_nicht_verworfen() -> None:
    sm = _sm()
    best = sm._pick_hunt_target([_cq("DL1AAA"), _cq("DL2BBB", dt=5.0)])
    assert best is not None and best.call_from == "DL1AAA"
    p = {k["call"]: k for k in _protokoll(sm)}
    assert p["DL1AAA"]["gewaehlt"] is True
    assert p["DL1AAA"]["verworfen_von"] is None


def test_genau_ein_gewinner_wenn_gepickt() -> None:
    sm = _sm()
    sm._pick_hunt_target([_cq("DL1AAA", snr=-5), _cq("DL2BBB", snr=-9), _cq("DL3CCC", snr=-12)])
    gew = [k for k in _protokoll(sm) if k["gewaehlt"]]
    assert len(gew) == 1


def test_merkmale_kommen_mit() -> None:
    """Ohne Signal, Grid und Beleg liesse sich spaeter nichts nachrechnen."""
    sm = _sm()
    sm.ctx.psk_heard_us = {"DL1AAA"}
    sm.ctx.call_to_continent = {"DL1AAA": "EU"}
    sm._pick_hunt_target([_cq("DL1AAA", snr=-11, grid="JO31")])
    k = _protokoll(sm)[0]
    assert k["snr_db"] == -11 and k["grid"] == "JO31"
    assert k["psk_heard"] is True and k["continent"] == "EU"


# ------------------------------------------------------- erster Filter zaehlt

def test_erster_verwerfender_filter_bleibt_stehen() -> None:
    """Ein Kandidat kann mehrere Filter reissen — es zaehlt der erste.

    Sonst stuende beim Nachrechnen der letzte Filter der Kette da, und
    der hat den Kandidaten nie gesehen.
    """
    sm = _sm()
    # dt=5 faellt am dt_fenster; snr=-25 wuerde spaeter auch am Floor scheitern,
    # aber der Floor kommt VOR dem dt-Fenster: also muss snr_floor stehen.
    sm._pick_hunt_target([_cq("DL1AAA"), _cq("DL2BBB", snr=-25, dt=5.0)])
    p = {k["call"]: k for k in _protokoll(sm)}
    assert p["DL2BBB"]["verworfen_von"] == "snr_floor"


# -------------------------------------------------- Einzelkandidat abgelehnt

def test_abgelehnter_einzelkandidat_traegt_den_grund() -> None:
    """Die beiden len==1-Regeln buchten bisher nur 'eins zu null'."""
    sm = _sm()
    sm.ctx.hunt_snr_floor_db = -22
    sm.ctx.hunt_sole_min_snr_db = -16
    sm.ctx.hunt_weak_requires_psk = False      # nur die Einzelregel pruefen
    best = sm._pick_hunt_target([_cq("DL1AAA", snr=-19)])
    assert best is None
    p = _protokoll(sm)
    assert len(p) == 1
    assert p[0]["verworfen_von"] == "einzelner_schwacher_cq"


# ------------------------------------------------------ adaptive Ruecknahme

def test_ruecknahme_verwirft_niemanden() -> None:
    """Nimmt der Schwach-Filter sich zurueck, darf im Protokoll kein
    Kandidat als von ihm verworfen stehen — sonst wuerde die Auswertung
    spaeter Anrufe zaehlen, die es gar nicht gab."""
    sm = _sm()
    sm.ctx.hunt_weak_requires_psk = True
    sm.ctx.hunt_weak_snr_db = -13
    sm.ctx.schwach_arm = False                 # adaptiver Arm
    sm.ctx.hunt_sole_min_snr_db = -16
    sm._pick_hunt_target([_cq("DL1AAA", snr=-14)])
    p = _protokoll(sm)
    assert all(k["verworfen_von"] != "schwach_ohne_psk" for k in p)


# --------------------------------------------------------- kein Doppelschreiben

def test_protokoll_wird_nach_dem_abholen_geleert() -> None:
    """Der Orchestrator holt per pop. Laeuft on_decodes mehrfach je Slot,
    darf derselbe Slot nicht zweimal in die DB."""
    sm = _sm()
    sm._pick_hunt_target([_cq("DL1AAA")])
    erst = sm._last_pick_diag.pop("kandidaten", None)
    zweit = sm._last_pick_diag.pop("kandidaten", None)
    assert erst and not zweit
