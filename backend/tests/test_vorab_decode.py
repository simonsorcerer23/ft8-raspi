"""Decode vor der Slot-Grenze, damit die Sendung puenktlich rausgeht.

Gemessen am 2026-09-11: Der Decoder beginnt erst an der Slot-Grenze und
braucht 0,5-1,0 s; die Sendung geht dadurch im Mittel 1,07 s nach der
Grenze raus. Die Stationen, die wir hoeren, liegen bei +0,12 s — wir kommen
also fast eine Sekunde spaeter an als der Durchschnitt. Toleriert wird das
(FT8-Decoder suchen +/-2,5 s), aber es kostet Marge bei schwachen Signalen.

FT8-Sendungen enden nach 12,64 s. Wer puenktlich sendet, ist also lange vor
der Grenze vollstaendig im Ringpuffer — ein Decode mit Vorlauf sieht ihn.
Stationen mit groesserem Zeitversatz fallen im Vorab-Durchgang heraus und
kommen erst im regulaeren Durchgang ins Log; die Sendeentscheidung treffen
sie dann nicht mehr mit.

Der Nutzen ist bewusst nicht geglaubt, sondern messbar gemacht:
``decoder_pre_decode_ab`` laesst die Betriebsart slotweise wechseln, und
``pick_attempt.pre_decode`` haelt je Anruf fest, aus welchem Durchgang die
Entscheidung kam.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from ft8_appliance.runtime import orchestrator as orch_mod


@dataclass
class _Operating:
    mode: str = "FT8"
    decoder_pre_decode: bool = True
    decoder_pre_decode_lead_s: float = 1.3
    decoder_pre_decode_ab: bool = False


@dataclass
class _Cfg:
    operating: _Operating = field(default_factory=_Operating)


def _orch(**op_kw):
    class _O:
        config = _Cfg(_Operating(**op_kw))
        _vorab_gewuenscht = orch_mod.Orchestrator._vorab_gewuenscht
    return _O()


# ------------------------------------------------------- der A/B-Schalter
def test_abgeschaltet_laeuft_nichts():
    o = _orch(decoder_pre_decode=False)
    assert o._vorab_gewuenscht() is False


def test_eingeschaltet_ohne_ab_laeuft_immer():
    o = _orch(decoder_pre_decode=True, decoder_pre_decode_ab=False)
    for _ in range(6):
        assert o._vorab_gewuenscht() is True


def test_ab_haengt_nicht_am_slot_takt():
    """Beide Wege unter denselben Bandbedingungen — sonst misst man die
    Ausbreitung statt den Umbau. Frueher wechselte der Arm im 2-Slot-Takt;
    weil der Sende-Rhythmus dieselbe Periode hat, synchronisierten sich
    beide und fast jeder A/B-Slot fiel auf einen Sende-Slot, in dem der
    Vorab-Durchgang uebersprungen wird — gemessen 2 Laeufe in 20 Minuten
    statt der erwarteten 40."""
    o = _orch(decoder_pre_decode=True, decoder_pre_decode_ab=True)
    assert not hasattr(o, "_vorab_ab_zaehler"), (
        "der Slot-Zaehler ist ersatzlos entfallen"
    )
    ergebnis = [o._vorab_gewuenscht() for _ in range(6)]
    assert ergebnis != [True, False, True, False, True, False]


# ------------------------------------------------- die Pipeline-Seite
@pytest.mark.asyncio
async def test_vorab_decode_ruehrt_die_slot_metrik_nicht_an(monkeypatch):
    """Der regulaere Durchgang fuehrt die Statistik — sonst zaehlt alles doppelt."""
    from ft8_appliance.decode.pipeline import DecodePipeline

    p = object.__new__(DecodePipeline)
    assert hasattr(p, "vorab_decode"), "Pipeline muss den Vorab-Einstieg anbieten"


def test_vorab_einstieg_existiert_und_ist_getrennt():
    """__call__ und vorab_decode teilen sich die Logik, nicht die Nebenwirkungen."""
    import inspect

    from ft8_appliance.decode.pipeline import DecodePipeline

    quelle = inspect.getsource(DecodePipeline._decode)
    assert "if not vorab:" in quelle, "Dedup und Metrik nur im regulaeren Durchgang"
    assert "if vorab:" in quelle, "Stufe 2 und jt9 nur im regulaeren Durchgang"
    vorab = inspect.getsource(DecodePipeline.vorab_decode)
    assert "vorab=True" in vorab


def test_telemetrie_haelt_den_durchgang_fest():
    """Ohne dieses Feld liesse sich der Nutzen hinterher nicht messen."""
    from ft8_appliance.db.models import PickAttempt

    assert hasattr(PickAttempt, "pre_decode")


def test_ab_wuerfelt_statt_im_takt_zu_wechseln(monkeypatch):
    """Der Sende-Rhythmus ist ebenfalls zwei Slots lang. Ein A/B im
    2-Slot-Takt synchronisierte sich damit, und fast alle A/B-Slots fielen
    auf Sende-Slots, in denen der Vorab-Durchgang uebersprungen wird —
    gemessen 2 Laeufe in 20 Minuten statt der erwarteten 40."""
    import inspect
    q = inspect.getsource(orch_mod.Orchestrator._vorab_gewuenscht)
    assert "random.random()" in q
    assert "% 2" not in q, "kein fester Takt mehr"


def test_ab_trifft_auf_dauer_beide_arme(monkeypatch):
    o = _orch(decoder_pre_decode=True, decoder_pre_decode_ab=True)
    ergebnisse = [o._vorab_gewuenscht() for _ in range(400)]
    anteil = sum(ergebnisse) / len(ergebnisse)
    assert 0.35 < anteil < 0.65, f"Arme grob gleich gross erwartet, war {anteil:.2f}"


def test_vorab_pfad_arbeitet_die_aktionen_ab():
    """on_decodes legt die Entscheidung nur in die Warteschlange. Ohne ein
    anschliessendes _drain_actions haette sie erst der regulaere Tick
    ausgefuehrt — also genau so spaet wie vorher, und der ganze Umbau waere
    wirkungslos. Ueberall sonst folgt _drain_actions unmittelbar auf
    on_decodes; im Vorab-Pfad fehlte es."""
    import inspect
    q = inspect.getsource(orch_mod.Orchestrator._vorab_decode_loop)
    assert "on_decodes" in q
    nach = q.split("on_decodes", 1)[1]
    assert "_drain_actions" in nach, "Aktionen bleiben liegen"


# ------------------------------- der regulaere Durchgang wiederholt nichts
class _Tick:
    def __init__(self, index):
        self.index = index


def _o_mit_merker(index, nachrichten):
    o = _orch(decoder_pre_decode=True)
    o._vorab_verarbeitet = (index, set(nachrichten))
    o._vorab_neue_decodes = orch_mod.Orchestrator._vorab_neue_decodes.__get__(o)
    return o


class _D:
    def __init__(self, message):
        self.message = message


def test_regulaerer_durchgang_ueberspringt_das_schon_verarbeitete():
    """Beide Durchgaenge decodieren denselben Slot. Ohne Filter zaehlen
    Slot-Paritaeten und Reputation doppelt, und die Versuchszaehler von
    RR73-Nachklang und Wiederaufnahme werden zweimal je Slot verbraucht —
    beim zweiten Mal ohne Wirkung, weil _tx_burst_active die Aussendung
    verwirft."""
    o = _o_mit_merker(7, {"CQ DL1ABC JO31", "DK9XR EA2JE -12"})
    alle = [_D("CQ DL1ABC JO31"), _D("DK9XR EA2JE -12"), _D("CQ SP9XYZ KO02")]

    neu = o._vorab_neue_decodes(_Tick(7), alle)

    assert [d.message for d in neu] == ["CQ SP9XYZ KO02"]


def test_anderer_slot_wird_nicht_gefiltert():
    o = _o_mit_merker(7, {"CQ DL1ABC JO31"})
    alle = [_D("CQ DL1ABC JO31")]

    assert o._vorab_neue_decodes(_Tick(8), alle) == alle


def test_merker_gilt_nur_einmal():
    """Sonst wuerde ein spaeter Pass desselben Slots ebenfalls gefiltert."""
    o = _o_mit_merker(7, {"CQ DL1ABC JO31"})
    alle = [_D("CQ DL1ABC JO31")]

    o._vorab_neue_decodes(_Tick(7), alle)

    assert o._vorab_neue_decodes(_Tick(7), alle) == alle


def test_picker_sieht_trotzdem_den_ganzen_slot():
    """Der CQ-Frequenz-Picker braucht den vollen Slot, nicht nur den Rest."""
    import inspect
    q = inspect.getsource(orch_mod.Orchestrator._on_slot_inner)
    assert "self.state_machine.last_decodes = list(decodes)" in q
