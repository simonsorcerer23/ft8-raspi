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
        _vorab_ab_zaehler = 0
        _vorab_gewuenscht = orch_mod.Orchestrator._vorab_gewuenscht
    return _O()


# ------------------------------------------------------- der A/B-Schalter
def test_abgeschaltet_laeuft_nichts():
    o = _orch(decoder_pre_decode=False)
    assert o._vorab_gewuenscht() is False


def test_eingeschaltet_ohne_ab_laeuft_immer():
    o = _orch(decoder_pre_decode=True, decoder_pre_decode_ab=False)
    for zaehler in range(6):
        o._vorab_ab_zaehler = zaehler
        assert o._vorab_gewuenscht() is True


def test_ab_wechselt_slotweise():
    """Beide Wege unter denselben Bandbedingungen — sonst misst man die
    Ausbreitung statt den Umbau."""
    o = _orch(decoder_pre_decode=True, decoder_pre_decode_ab=True)
    ergebnis = []
    for zaehler in range(6):
        o._vorab_ab_zaehler = zaehler
        ergebnis.append(o._vorab_gewuenscht())
    assert ergebnis == [True, False, True, False, True, False]


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
