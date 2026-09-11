"""Der Alleingang an ein weit entferntes Ziel ist der teuerste Leerlauf.

Gemessen ueber zwei Tage Hunting (246 eigene Anrufe, 10.9. abends bis
11.9. nachmittags): Der Tier ``sole`` — der einzige Kandidat im Slot, es
gibt also keine Alternative — liefert

    < 2000 km     128 Anrufe   35 QSOs   27,3 %   194 min Sendezeit
    2000-4000 km   28 Anrufe    5 QSOs   17,9 %    37 min
    > 4000 km      49 Anrufe    1 QSO     2,0 %    69 min

Also 5,5 Minuten je QSO im Nahbereich gegen 69 Minuten im Fernbereich.

Die naheliegende Erklaerung "totes Band" traegt nicht. ``sole`` heisst ja
gerade, dass kaum jemand zu hoeren ist — der Verdacht lag also nahe, dass
nicht die Entfernung schuld ist, sondern die Bandbedingung. Bei
vergleichbar ruhigem Band (hoechstens fuenf Decodes im Slot) sind es aber
**0 von 32** gegen 15 von 64 im Nahbereich.

Und 53 der 54 Fernziele waren kein neues DXCC — es ging also auch nicht um
seltene Laender.

Bewusst nur im Alleingang: Gab es eine Wahl, gingen drei von fuenf
Fernzielen durch. Dort ist die Entfernung kein Ausschluss, hoechstens ein
Rangkriterium.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ft8_appliance.statemachine.machine import StateMachine
from ft8_appliance.statemachine.states import DecodedMsg, MachineContext


def _sm(*, gate=True, arm=True, km=4000) -> StateMachine:
    """Ein Kontext wie im Betrieb: Die Award-Mengen sind gefuellt.

    Wichtig fuer die Aussagekraft — in einem leeren Kontext gilt jedes Grid
    als neu, und damit jedes Ziel als Award-Pick. Das Gate koennte dann gar
    nicht greifen, und der Test wuerde nur sein eigenes Artefakt messen.
    In den gemessenen 49 Faellen gewann der Tier ``sole``, es gab dort also
    tatsaechlich kein Award-Signal.
    """
    sm = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58td"))
    sm.ctx.hunt_sole_dx_gate = gate
    sm.ctx.hunt_sole_dx_arm = arm
    sm.ctx.hunt_sole_dx_km = km
    sm.ctx.worked_grids = {"FN20", "JO31"}
    sm.ctx.worked_grid_band = {("FN20", "20m"), ("JO31", "20m")}
    sm.ctx.worked_dxcc_band = {("United States", "20m"), ("Fed. Rep. of Germany", "20m")}
    return sm


def _cq(call: str, grid: str | None) -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call, call_to=None, grid=grid,
        message=f"CQ {call} {grid or ''}".strip(), snr_db=-12, dt_s=0.2,
        freq_offset_hz=1500, band="20m",
    )


# JN58td = Muenchen. FN20 = Washington DC (~6800 km), JO31 = Duesseldorf (~500 km)
FERN = _cq("W3ABC", "FN20")
NAH = _cq("DL1ABC", "JO31")


def test_fernziel_im_alleingang_wird_gesperrt():
    assert _sm()._ist_aussichtsloses_fernziel(FERN) is True


def test_nahziel_bleibt_unberuehrt():
    assert _sm()._ist_aussichtsloses_fernziel(NAH) is False


def test_abgeschaltetes_gate_sperrt_nichts():
    assert _sm(gate=False)._ist_aussichtsloses_fernziel(FERN) is False


def test_anderer_ab_arm_sperrt_nichts():
    """Der Vergleichsarm muss unveraendert weiterarbeiten."""
    assert _sm(arm=False)._ist_aussichtsloses_fernziel(FERN) is False


def test_neues_dxcc_geht_immer_durch():
    """53 der 54 gemessenen Fernziele waren kein neues DXCC — das eine, das
    es war, soll weiterhin angerufen werden."""
    sm = _sm()
    sm.ctx.new_dxcc_calls = {"W3ABC"}

    assert sm._ist_aussichtsloses_fernziel(FERN) is False


def test_ohne_bekannte_entfernung_wird_nie_gesperrt():
    """Im Zweifel rufen wir an."""
    assert _sm()._ist_aussichtsloses_fernziel(_cq("W3ABC", None)) is False


def test_schwelle_ist_einstellbar():
    """JO31 liegt 499 km entfernt — unter der Vorgabe von 4000 km harmlos,
    unter einer Schwelle von 400 km nicht mehr."""
    assert _sm(km=400)._ist_aussichtsloses_fernziel(NAH) is True
    assert _sm(km=600)._ist_aussichtsloses_fernziel(NAH) is False


# ------------------------------------------------- der Picker als Ganzes
def _picker_sm(**kw) -> StateMachine:
    sm = _sm(**kw)
    sm.ctx.auto_answer = True
    sm.ctx.hunt_snr_floor_db = -22
    return sm


def test_picker_verwirft_das_einsame_fernziel():
    sm = _picker_sm()

    assert sm._pick_hunt_target([FERN]) is None
    assert sm._last_pick_diag.get("winning_tier") == "sole_dx_rejected"


def test_picker_nimmt_das_einsame_nahziel():
    """Die Gegenprobe — sonst koennte der Test oben auch bei einem Picker
    bestehen, der gar nichts mehr auswaehlt."""
    sm = _picker_sm()

    assert sm._pick_hunt_target([NAH]) is NAH


def test_bei_auswahl_bleibt_das_fernziel_im_rennen():
    """Gab es eine Wahl, gingen drei von fuenf Fernzielen durch — dort ist
    die Entfernung kein Ausschluss. Das Gate greift nur im Alleingang."""
    sm = _picker_sm()

    gewaehlt = sm._pick_hunt_target([FERN, NAH])

    assert gewaehlt is not None
    assert sm._last_pick_diag.get("winning_tier") != "sole_dx_rejected"


def test_der_vergleichsarm_ruft_weiter_an():
    """Ohne diesen Arm gaebe es nichts zu vergleichen."""
    sm = _picker_sm(arm=False)

    assert sm._pick_hunt_target([FERN]) is FERN


def test_filterstufe_wird_gezaehlt():
    """Sonst waere im Betrieb nicht zu sehen, wie oft das Gate greift."""
    sm = _picker_sm()

    sm._pick_hunt_target([FERN])

    assert sm.filter_drops.get("fernziel_allein") == 1


# --------------------------------------------------------- der A/B-Arm
def _orch_arm(**op_kw):
    from ft8_appliance.runtime import orchestrator as orch_mod

    class _Op:
        def __init__(self, **kw):
            self.hunt_sole_dx_gate = kw.get("hunt_sole_dx_gate", True)
            self.hunt_sole_dx_ab = kw.get("hunt_sole_dx_ab", True)

    class _Cfg:
        def __init__(self, op):
            self.operating = op

    class _O:
        config = _Cfg(_Op(**op_kw))
        state_machine = type("S", (), {"ctx": MachineContext(callsign="DK9XR", my_grid="JN58td")})()
        _fern_arm_slot = -1
        _fern_arm = False
        _FERN_ARM_BLOCK_S = orch_mod.Orchestrator._FERN_ARM_BLOCK_S
        _setze_fern_gate_arm = orch_mod.Orchestrator._setze_fern_gate_arm

    return _O()


def test_arm_bleibt_ueber_viele_slots_stabil():
    """Die Wirkungskette ist laenger als ein Slot: Das Gate greift in Slot
    N, der CQ-Fallback startet nach zwei Slots ohne Pick, die Antwort kommt
    noch spaeter. Wuerfelte der Arm je Slot, wuerde der eingehende Anruf
    dem Arm seines Ankunfts-Slots zugeschrieben statt dem, der den CQ-Ruf
    veranlasst hat — der Effekt verteilte sich gleichmaessig auf beide
    Arme, und der A/B waere wertlos."""
    o = _orch_arm()

    o._setze_fern_gate_arm(7)
    erster = o.state_machine.ctx.hunt_sole_dx_arm
    for slot in range(8, 40):
        o._setze_fern_gate_arm(slot)
        assert o.state_machine.ctx.hunt_sole_dx_arm is erster, (
            "Arm darf innerhalb des Blocks nicht wechseln"
        )


def test_arm_wechselt_mit_dem_zeitblock(monkeypatch):
    from ft8_appliance.runtime import orchestrator as orch_mod

    o = _orch_arm()
    uhr = {"t": 1_000_000.0}
    monkeypatch.setattr(orch_mod.time, "time", lambda: uhr["t"])

    arme = []
    for _ in range(40):
        o._setze_fern_gate_arm(0)
        arme.append(o.state_machine.ctx.hunt_sole_dx_arm)
        uhr["t"] += o._FERN_ARM_BLOCK_S

    assert 0.30 < sum(arme) / len(arme) < 0.70, "beide Arme grob gleich oft"
    assert len(set(arme)) == 2, "der Arm muss ueber die Bloecke wechseln"


def test_block_ist_kurz_genug_fuer_vergleichbare_ausbreitung():
    """Lang genug, dass Gate, CQ-Ruf und Antwort im selben Arm liegen;
    kurz genug, dass sich die Bandbedingungen zwischen den Armen nicht
    wesentlich unterscheiden."""
    from ft8_appliance.runtime import orchestrator as orch_mod

    assert 5 * 60 <= orch_mod.Orchestrator._FERN_ARM_BLOCK_S <= 30 * 60


def test_ohne_ab_gilt_das_gate_immer():
    o = _orch_arm(hunt_sole_dx_ab=False)

    for slot in range(6):
        o._setze_fern_gate_arm(slot)
        assert o.state_machine.ctx.hunt_sole_dx_arm is True


def test_abgeschaltetes_gate_setzt_keinen_arm():
    o = _orch_arm(hunt_sole_dx_gate=False)

    o._setze_fern_gate_arm(3)

    assert o.state_machine.ctx.hunt_sole_dx_arm is False


# ------------------------------- die Kette bis zum CQ-Fallback
def test_gate_treibt_den_slot_in_den_cq_fallback():
    """Daran haengt der ganze Nutzen: Der Slot soll nicht verfallen,
    sondern als CQ-Ruf zurueckkommen. Ohne diese Kette waere das Gate
    schlechter als nichts — wir wuerden einfach schweigen."""
    from ft8_appliance.statemachine.guards import HardwareState
    from ft8_appliance.statemachine.states import State

    hw = HardwareState(
        gps_fix_mode=3, time_offset_s=0.05, swr=1.2, alc_pct=0,
        battery_v=12.0, cpu_temp_c=45.0, audio_drift_samples=0,
        antenna_covers_band=True, chrony_synced=True,
    )
    sm = _picker_sm()
    sm.state = State.IDLE
    sm.ctx.auto_answer = True
    sm.ctx.hunt_cq_fallback = True
    sm.ctx.hunt_cq_fallback_after_slots = 2
    sm.ctx.idle_slots_without_pick = 0

    # Zwei Slots, in denen nur das aussichtslose Fernziel zu hoeren ist
    sm.on_decodes(hw, [FERN])
    assert sm.ctx.idle_slots_without_pick == 1
    sm.on_decodes(hw, [FERN])
    assert sm.ctx.idle_slots_without_pick == 2

    sm.on_slot_tick(hw, None)

    assert sm.state is State.CQ_CALLING
    assert sm.ctx.cq_fallback_active is True


def test_ohne_gate_bliebe_der_zaehler_stehen():
    """Die Gegenprobe: Im Vergleichsarm wird angerufen, der Slot geht also
    nicht an den CQ-Fallback."""
    from ft8_appliance.statemachine.guards import HardwareState
    from ft8_appliance.statemachine.states import State

    hw = HardwareState(
        gps_fix_mode=3, time_offset_s=0.05, swr=1.2, alc_pct=0,
        battery_v=12.0, cpu_temp_c=45.0, audio_drift_samples=0,
        antenna_covers_band=True, chrony_synced=True,
    )
    sm = _picker_sm(arm=False)
    sm.state = State.IDLE
    sm.ctx.auto_answer = True

    sm.on_decodes(hw, [FERN])

    assert sm.ctx.idle_slots_without_pick == 0
    assert sm.state is not State.IDLE
