"""Der Picker-Kontext muss zu den Kandidaten passen, die gerade rufen.

Kontinent, DXCC, Standort und Rarity werden pro Slot aus den Decodes
gebaut. Bis 2026-09-11 geschah das in ``_refresh_hardware_state`` — dem
*ersten* Schritt des Slots, bevor der Decoder gefragt war. Gearbeitet wurde
deshalb mit ``self._last_decodes``, den Rufern des vorigen Slots.

Weil FT8-Stationen abwechselnd senden, sind das groesstenteils andere
Stationen: An diesem Tag waren nur 5,3 % der CQ-Rufer eines Slots schon im
Slot davor zu hoeren (zwei Slots davor, also gleiche Paritaet: 31 %).

Die Folgen liessen sich messen: Das Kontinent-Gate stand scharf, hatte eine
gueltige Quote fuer Nordamerika (4 %, Schwelle 5 %) — und liess trotzdem an
einem Tag 14 Anrufe dorthin durch, ohne ein einziges Mal zu filtern. Die
Pile-Up-Erkennung zaehlte ebenfalls die Anrufer des falschen Slots; das
groesste je erkannte Pile-Up lag bei vier, die Schwelle bei fuenf.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ft8_appliance.runtime import orchestrator as orch_mod


def _decode(call: str, message: str | None = None, grid: str | None = "FN20"):
    from ft8_appliance.statemachine.states import DecodedMsg
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call, call_to=None, grid=grid,
        message=message or f"CQ {call} FN20", snr_db=-8, dt_s=0.2,
        freq_offset_hz=1500, band="20m",
    )


class _Entity:
    def __init__(self, name, continent, lat=40.0, lon=-74.0):
        self.name = name; self.continent = continent
        self.lat = lat; self.lon = lon


class _Rec:
    def __init__(self, entity): self.entity = entity


class _Cty:
    """Erkennt W… als Nordamerika, DL… als Europa."""
    def lookup(self, call):
        call = (call or "").upper()
        if call.startswith("W"):
            return _Rec(_Entity("United States", "NA"))
        if call.startswith("DL"):
            return _Rec(_Entity("Fed. Rep. of Germany", "EU", 51.0, 10.0))
        return None


def _orch(monkeypatch):
    from ft8_appliance.statemachine.machine import StateMachine
    from ft8_appliance.statemachine.states import MachineContext

    class _Integrations:
        cty = _Cty()

    class _O:
        integrations = _Integrations()
        state_machine = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58"))
        _worked_dxccs: set = set()
        _last_decodes: list = []
        _active_continent_hours: set = set()
        _band_conditions_day: dict = {}
        _band_conditions_night: dict = {}
        _watchlist_calls: set = set()
        _soft_blacklist: set = set()
        _op_slot_parity: dict = {}
        _worked_call_band: set = set()
        _freq_reputation: dict = {}
        _refresh_decode_context = orch_mod.Orchestrator._refresh_decode_context
        _detect_pile_ups = orch_mod.Orchestrator._detect_pile_ups
        _context_health = orch_mod.Orchestrator._context_health
    return _O()


@pytest.mark.asyncio
async def test_kontext_stammt_aus_den_uebergebenen_decodes(monkeypatch):
    o = _orch(monkeypatch)
    o._last_decodes = [_decode("DL9ALT")]          # voriger Slot

    await o._refresh_decode_context([_decode("W1NEU")])

    ctx = o.state_machine.ctx
    assert ctx.call_to_continent.get("W1NEU") == "NA", "der aktuelle Rufer muss drin sein"
    assert "DL9ALT" not in ctx.call_to_continent, "der alte Slot darf nicht nachwirken"


@pytest.mark.asyncio
async def test_kontinent_steht_fuer_das_gate_bereit(monkeypatch):
    """Genau das fehlte: ohne Eintrag laesst das Gate jeden durch."""
    o = _orch(monkeypatch)

    await o._refresh_decode_context([_decode("W2XYZ"), _decode("DL1ABC")])

    ctx = o.state_machine.ctx
    assert ctx.call_to_continent == {"W2XYZ": "NA", "DL1ABC": "EU"}


@pytest.mark.asyncio
async def test_standort_und_dxcc_kommen_mit(monkeypatch):
    o = _orch(monkeypatch)

    await o._refresh_decode_context([_decode("DL1ABC")])

    ctx = o.state_machine.ctx
    assert ctx.call_to_dxcc.get("DL1ABC") == "Fed. Rep. of Germany"
    # Seit 2026-09-12 hat der Grid aus dem Decode Vorrang vor der
    # Landesmitte aus cty.dat: FN20 liegt bei (40,5 / -75,0), die
    # Landesmitte von Deutschland bei (51 / 10). Ueber 1223 echte Ziele
    # gemessen lag die Landesmitte fuer Nordamerika im Median 1130 km
    # neben dem Grid — fuer ein Grayline-Fenster von einer halben Stunde
    # ist das eine Stunde Sonnenzeit daneben.
    assert ctx.call_to_latlon.get("DL1ABC") == (40.5, -75.0)


@pytest.mark.asyncio
async def test_ohne_grid_bleibt_die_landesmitte(monkeypatch):
    """Kein Grid im Decode → cty.dat liefert, wie vorher."""
    o = _orch(monkeypatch)
    await o._refresh_decode_context([_decode("DL1ABC", grid=None)])
    assert o.state_machine.ctx.call_to_latlon.get("DL1ABC") == (51.0, 10.0)


@pytest.mark.asyncio
async def test_kaputter_grid_faellt_auf_landesmitte_zurueck(monkeypatch):
    """Ein unbrauchbarer Locator darf den Kontext nicht leer lassen."""
    o = _orch(monkeypatch)
    await o._refresh_decode_context([_decode("DL1ABC", grid="ZZ")])
    assert o.state_machine.ctx.call_to_latlon.get("DL1ABC") == (51.0, 10.0)


@pytest.mark.asyncio
async def test_nur_cq_rufe_zaehlen(monkeypatch):
    """Wer schon im QSO steckt, ist kein Kandidat."""
    o = _orch(monkeypatch)
    laufend = _decode("W3BUSY", message="DL1ABC W3BUSY -12")
    laufend.call_to = "DL1ABC"

    await o._refresh_decode_context([laufend])

    assert o.state_machine.ctx.call_to_continent == {}


@pytest.mark.asyncio
async def test_leerer_slot_leert_den_kontext(monkeypatch):
    o = _orch(monkeypatch)
    await o._refresh_decode_context([_decode("W1NEU")])

    await o._refresh_decode_context([])

    assert o.state_machine.ctx.call_to_continent == {}


@pytest.mark.asyncio
async def test_fuellstand_ist_ablesbar(monkeypatch):
    """context_health beantwortet: steht ueberhaupt eine Datenbasis?"""
    o = _orch(monkeypatch)
    await o._refresh_decode_context([_decode("W1NEU"), _decode("DL1ABC")])

    gesundheit = o._context_health()

    assert gesundheit["kontinent_je_call"] == 2
    assert gesundheit["dxcc_je_call"] == 2
    assert "slot_paritaet_gelernt" in gesundheit
    assert "psk_hoert_uns" in gesundheit
