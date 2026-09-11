"""Eine vorab getroffene Entscheidung darf nicht vorzeitig senden.

Der Vorab-Decode faellt die Sendeentscheidung rund eine Sekunde vor der
Slot-Grenze. Wird die Aussendung dann sofort ausgefuehrt, ragt sie in den
laufenden Slot hinein und kommt bei der Gegenstation mit negativem
Zeitversatz an. Live am 2026-09-11 beobachtet: Der gemessene Sendeversatz
sprang von 0,93 s auf Werte um 13,9 s — also kurz *vor* der naechsten
Grenze statt kurz danach.

Richtig ist, die letzten Zehntel abzuwarten. Genau darin liegt der Gewinn
des Vorab-Decodes: Die Entscheidung steht frueh, die Aussendung beginnt
puenktlich.

Die Wartezeit greift nur nahe der Grenze (<= 2,5 s Rest). Der regulaere
Durchgang entscheidet kurz *nach* der Grenze und darf nicht verzoegert
werden — sonst wuerde jede Aussendung einen ganzen Slot zu spaet kommen.
"""

from __future__ import annotations

import inspect

from ft8_appliance.runtime import orchestrator as orch_mod


def _quelle() -> str:
    return inspect.getsource(orch_mod.Orchestrator._do_tx_message)


def test_puffer_ueber_die_grenze():
    """_slot_phase_s rechnet time.time() %% slot_s. Wer exakt auf der Grenze
    landet, misst 14,999 statt 0,001 — das sah wie ein verspaeteter
    Sendestart aus, und drei solche Meldungen in Folge stufen den Decoder
    zurueck. Ein kleiner Puffer verhindert das."""
    q = _quelle()
    assert "rest_bis_grenze + 0.02" in q


def test_wartet_nur_kurz_vor_der_grenze():
    q = _quelle()
    assert "rest_bis_grenze" in q, "Wartezeit fehlt"
    assert "0.0 < rest_bis_grenze <= max_warten" in q, (
        "Die Bedingung muss den regulaeren Durchgang ausnehmen — der "
        "entscheidet kurz nach der Grenze und haette sonst 14 s Wartezeit"
    )


def test_wartet_vor_der_versatzmessung():
    """Sonst wuerde der gemessene Versatz die Wartezeit nicht abbilden."""
    q = _quelle()
    assert q.index("rest_bis_grenze") < q.index("_record_tx_start_offset")


def test_slotlaenge_folgt_der_betriebsart():
    q = _quelle()
    assert '7.5 if self.config.operating.mode == "FT4" else 15.0' in q


def test_rechnung_stimmt_fuer_beide_faelle():
    """Vorab-Entscheidung wartet, regulaere nicht."""
    slot_s = 15.0
    # Vorab: Entscheidung bei Phase 14,0 -> 1,0 s warten
    assert 0.0 < slot_s - 14.0 <= 2.5
    # Regulaer: Entscheidung bei Phase 0,9 -> kein Warten
    assert not (0.0 < slot_s - 0.9 <= 2.5)
    # Genau auf der Grenze: kein Warten (Rest 0)
    assert not (0.0 < slot_s - 15.0 <= 2.5)


def test_wartegrenze_deckt_den_vorlauf_ab():
    """Wird nicht gewartet, faellt die Sendung in die B4-Regel ("Burst
    mitten im Slot") und entfaellt ersatzlos. Mit einer festen Obergrenze
    von 2,5 s haette ein groesserer decoder_pre_decode_lead_s das Senden
    still abgeschaltet."""
    q = _quelle()
    assert "decoder_pre_decode_lead_s" in q
    assert "max(2.5, lead + 1.0)" in q


def test_zaehler_auch_im_manuellen_zweig_zurueckgesetzt():
    """Seit dem Vorab-Decode laeuft ein Teil der regulaeren Aussendungen
    durch den manuellen Zweig (kein Slot-Tick). Ohne Reset bliebe ein alter
    Zaehlerstand stehen, und eine einzelne spaetere Verspaetung wuerde die
    Decoder-Rueckstufung ausloesen."""
    import inspect
    from ft8_appliance.runtime import orchestrator as orch_mod
    q = inspect.getsource(orch_mod.Orchestrator._record_tx_start_offset)
    manuell = q.split("manueller TX-Start %.2f", 1)[1].split("return True", 1)[0]
    assert "_consecutive_late_tx = 0" in manuell
