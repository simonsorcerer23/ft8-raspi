"""Ein RR73 an einen Dritten ist keine Bestaetigung an uns.

Zwischen dem 11. und 14.09.2026 hat die Station vier QSOs geloggt, die
nie stattfanden. Das Muster war jedesmal gleich: Wir rufen eine Station
an, sie schliesst ihr laufendes QSO mit einem Dritten ab, und weil der
Decoder dessen Rufzeichen nicht aufloesen konnte ("<...> SM6/DL1HTW
RR73"), las unsere Maschine das als Bestaetigung an uns.

Belegt bei SM6/DL1HTW: 30 Sekunden nach dem RR73 kam
"<HB9CEX> SM6/DL1HTW 73" — er arbeitete HB9CEX, nicht uns.
"""
from __future__ import annotations

from datetime import UTC, datetime

from ft8_appliance.statemachine.machine import (
    _find_closing, _find_report_from_them, _hashed_match, _ist_standard_call,
)
from ft8_appliance.statemachine.states import DecodedMsg


def _d(call_from: str, call_to: str | None, message: str) -> DecodedMsg:
    return DecodedMsg(
        ts=datetime.now(UTC), call_from=call_from, call_to=call_to, grid=None,
        message=message, snr_db=-12, dt_s=0.2, freq_offset_hz=1500, band="20m",
    )


# ------------------------------------------------- Standardrufzeichen

def test_standardrufzeichen_werden_erkannt() -> None:
    for call in ("DK9XR", "DO3XR", "W1AW", "R4IK", "OH3OJ", "G0ABC"):
        assert _ist_standard_call(call), call


def test_zusammengesetzte_sind_kein_standard() -> None:
    """Nur diese werden in FT8 gehasht."""
    for call in ("SM6/DL1HTW", "DK9XR/P", "EK/RX3DPK", "IS0/IK2YCW", "SM0FVI/3"):
        assert not _ist_standard_call(call), call


# --------------------------------------------------- der eigentliche Bug

def test_fremdes_rr73_gilt_nicht_als_unseres() -> None:
    """Der Fall SM6/DL1HTW vom 14.09.2026, 09:24:30 UTC."""
    decodes = [_d("SM6/DL1HTW", "<...>", "<...> SM6/DL1HTW RR73")]
    assert _find_closing(decodes, "SM6/DL1HTW", "DK9XR") is False


def test_alle_vier_faelle_vom_september() -> None:
    for partner in ("AO77R", "SV8/F6BLP", "SM0FVI/3", "SM6/DL1HTW"):
        decodes = [_d(partner, "<...>", f"<...> {partner} RR73")]
        assert _find_closing(decodes, partner, "DK9XR") is False, partner


def test_auch_ein_fremder_report_zaehlt_nicht() -> None:
    decodes = [_d("SM6/DL1HTW", "<...>", "<...> SM6/DL1HTW -12")]
    assert _find_report_from_them(decodes, "SM6/DL1HTW", "DK9XR") is None


def test_echtes_rr73_an_uns_wird_weiter_erkannt() -> None:
    decodes = [_d("SM6/DL1HTW", "DK9XR", "DK9XR SM6/DL1HTW RR73")]
    assert _find_closing(decodes, "SM6/DL1HTW", "DK9XR") is True


# ----------------------------------- die Toleranz bleibt, wo sie zaehlt

def test_portabel_bleibt_tolerant() -> None:
    """Sendet die Station selbst zusammengesetzt, wird ihr Rufzeichen
    gehasht — dann muss die Wildcard weiter greifen, sonst gehen
    Antworten auf unsere eigenen Aussendungen verloren."""
    decodes = [_d("W1AW", "<...>", "<...> W1AW RR73")]
    assert _find_closing(decodes, "W1AW", "DK9XR/P") is True


def test_gehashter_partner_bleibt_tolerant() -> None:
    decodes = [_d("<...>", "DK9XR", "DK9XR <...> RR73")]
    assert _find_closing(decodes, "EK/RX3DPK", "DK9XR") is True


def test_beide_seiten_gehasht_bleibt_mehrdeutig() -> None:
    decodes = [_d("<...>", "<...>", "<...> <...> RR73")]
    assert _find_closing(decodes, "EK/RX3DPK", "DK9XR/P") is False


def test_hashed_match_direkt() -> None:
    assert _hashed_match("DK9XR", "DK9XR") is True
    assert _hashed_match("<...>", "DK9XR") is False, "Standardcall wird nie gehasht"
    assert _hashed_match("<...>", "DK9XR/P") is True
    assert _hashed_match(None, "DK9XR") is False
    assert _hashed_match("W1AW", "DK9XR") is False
    assert _hashed_match("W1AW", "DK9XR/P") is False, "fremder Call ist keine Wildcard"
