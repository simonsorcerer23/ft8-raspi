"""Die Aussendung wartet die Slot-Grenze ab.

Kommt die Entscheidung aus dem Vorab-Decode, steht sie *vor* der Grenze.
Wird dann sofort gesendet, ragt der Burst in den laufenden Slot und kommt
bei der Gegenstation mit negativem Zeitversatz an — live sichtbar als
Sendeversatz von 13,9 s statt 0,9 s. Genau dieses Warten ist der Gewinn des
Vorab-Decodes: Die Entscheidung ist frueh, die Aussendung beginnt puenktlich.

Zwei Feinheiten, beide teuer erkauft:

* Ein kleiner Puffer ueber die Grenze hinaus. ``_slot_phase_s`` rechnet
  ``time.time() % slot_s``; wer exakt auf der Grenze landet, misst 14,999
  statt 0,001. Das sah wie ein verspaeteter Sendestart aus — und drei solche
  Meldungen in Folge stufen den Decoder zurueck.
* Die Obergrenze der Wartezeit folgt dem Vorlauf. Das ist Absicherung, kein
  behobener Fehler: ``decoder_pre_decode_lead_s`` ist auf 2,5 s begrenzt,
  und bis dahin haetten feste 2,5 s immer gereicht. Der Zusammenhang soll
  aber im Code stehen und nicht nur in einer Feldvalidierung — ohne Warten
  faellt die Aussendung in die B4-Regel ("Burst mitten im Slot") und
  entfaellt ersatzlos.
"""

from __future__ import annotations

import pytest

from .test_wave2_timing import _cfg, _orch


def _orch_mit_laufender_uhr(phase_s: float, **operating):
    """Ein Orchestrator, dessen Slot-Phase durch das Warten weiterlaeuft."""
    o = _orch(_cfg(**operating) if operating else None)
    uhr = {"phase": phase_s}
    o._slot_phase_s = lambda: uhr["phase"]  # type: ignore[method-assign]
    o._in_slot_tick = False

    async def _schlaf(s):
        uhr["phase"] = (uhr["phase"] + s) % 15.0

    o._schlaf_ersatz = _schlaf
    o._uhr = uhr
    return o


async def _sende(o, monkeypatch):
    import ft8_appliance.runtime.orchestrator as m
    monkeypatch.setattr(m.asyncio, "sleep", o._schlaf_ersatz)
    await o._do_tx_message({"message": "CQ DK9XR JN58",
                            "freq_offset_hz": 1500, "kind": "cq"})


@pytest.mark.asyncio
async def test_kurz_vor_der_grenze_wird_gewartet(monkeypatch):
    """Der Fall des Vorab-Decodes: 0,5 s vor der Grenze entschieden."""
    o = _orch_mit_laufender_uhr(14.5)

    await _sende(o, monkeypatch)

    assert o._uhr["phase"] == pytest.approx(0.02, abs=1e-6)
    assert o._tx_sent_offsets_s == [pytest.approx(0.02, abs=1e-6)]


@pytest.mark.asyncio
async def test_ohne_warten_entfiele_die_aussendung(monkeypatch):
    """Die Gegenprobe: 14,5 s gemessen waeren ein Burst mitten im Slot."""
    o = _orch_mit_laufender_uhr(14.5)
    o._slot_phase_s = lambda: 14.5  # Warten wirkungslos

    await _sende(o, monkeypatch)

    assert o._tx_sent_offsets_s == [], "B4 haette verwerfen muessen"


@pytest.mark.asyncio
async def test_der_regulaere_durchgang_wartet_nicht(monkeypatch):
    """Er entscheidet kurz NACH der Grenze — sonst 14 s Wartezeit."""
    o = _orch_mit_laufender_uhr(0.9)
    o._in_slot_tick = True

    await _sende(o, monkeypatch)

    assert o._uhr["phase"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_puffer_haelt_die_phase_diesseits_der_grenze(monkeypatch):
    """Ohne ihn misst _slot_phase_s 14,999 statt 0,001 — drei solche
    Meldungen in Folge stufen den Decoder zurueck."""
    o = _orch_mit_laufender_uhr(14.999)

    await _sende(o, monkeypatch)

    assert o._uhr["phase"] < 1.0


@pytest.mark.asyncio
async def test_wartegrenze_folgt_dem_vorlauf(monkeypatch):
    """Beim hoechsten zulaessigen Vorlauf (2,5 s) deckt die Grenze 3,5 s
    ab. Heute unerreichbar — der Vorab-Durchgang braucht selbst Zeit, der
    Rest bleibt unter 2,5 s. Der Test haelt den Zusammenhang fest, falls
    die Feldvalidierung je gelockert wird."""
    o = _orch_mit_laufender_uhr(11.6, decoder_pre_decode_lead_s=2.5)

    await _sende(o, monkeypatch)

    assert o._uhr["phase"] == pytest.approx(0.02, abs=1e-6)
    assert o._tx_sent_offsets_s == [pytest.approx(0.02, abs=1e-6)]


@pytest.mark.asyncio
async def test_mitten_im_slot_wird_nicht_gewartet(monkeypatch):
    """Ein manueller Burst bei halbem Slot ist kein Vorab-Fall — er
    entfaellt nach B4, statt 7 s zu blockieren."""
    o = _orch_mit_laufender_uhr(7.5)

    await _sende(o, monkeypatch)

    assert o._uhr["phase"] == pytest.approx(7.5)
    assert o._tx_sent_offsets_s == []
