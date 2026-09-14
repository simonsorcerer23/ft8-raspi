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
from ft8_appliance.statemachine import (
    DecodedMsg,
    GuardLimits,
    HardwareState,
    MachineContext,
    StateMachine,
)


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


# ------------------------------------------------- Plausibilitaetssperre

def _sm() -> StateMachine:
    return StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58td"),
                        limits=GuardLimits())


def _hw() -> HardwareState:
    return HardwareState(gps_fix_mode=3, time_offset_s=0.01, swr=1.3,
                         alc_pct=0, battery_v=13.4, cpu_temp_c=55.0)


def test_ohne_antwort_an_uns_wird_nicht_geloggt() -> None:
    """Die zweite Verteidigungslinie: Selbst wenn ein Abschluss
    faelschlich als unserer gilt, verhindert die Sperre den Logeintrag.

    Nachgestellt ist der Ablauf vom 13.09.: Wir antworten auf ein CQ,
    die Station sendet nie an uns, aber ein Closing erreicht uns.
    """
    sm, hw = _sm(), _hw()
    cq = _d("SM0FVI/3", None, "CQ SM0FVI/3")
    sm.on_user_reply_to(hw, cq)
    sm.drain_actions()
    assert sm.qso is not None and sm.qso.partner_hat_uns_gerufen is False

    # Ein Abschluss, der an einen Dritten ging — hier bewusst so gebaut,
    # dass er die Sperre erreicht, falls die Rufzeichenpruefung versagt.
    sm.qso.partner_hat_uns_gerufen = False
    sm._emit_log_qso(hw)
    assert not any(a.kind == "LOG_QSO" for a in sm.drain_actions()), \
        "ohne eine einzige Sendung an uns darf nichts ins Log"


def test_mit_antwort_an_uns_wird_geloggt() -> None:
    sm, hw = _sm(), _hw()
    sm.on_user_reply_to(hw, _d("W1AW", None, "CQ W1AW"))
    sm.drain_actions()
    sm.on_decodes(hw, [_d("W1AW", "DK9XR", "DK9XR W1AW -12")])
    sm.drain_actions()
    assert sm.qso is not None and sm.qso.partner_hat_uns_gerufen is True


def test_flag_ueberlebt_slots_ohne_decode() -> None:
    """Einmal an uns gesendet reicht — der Abschluss kommt oft Slots spaeter."""
    sm, hw = _sm(), _hw()
    sm.on_user_reply_to(hw, _d("W1AW", None, "CQ W1AW"))
    sm.drain_actions()
    sm.on_decodes(hw, [_d("W1AW", "DK9XR", "DK9XR W1AW -12")])
    sm.drain_actions()
    sm.on_decodes(hw, [_d("DL1ABC", "DL2XYZ", "DL2XYZ DL1ABC 73")])
    assert sm.qso is not None and sm.qso.partner_hat_uns_gerufen is True


def test_fremde_station_setzt_das_flag_nicht() -> None:
    sm, hw = _sm(), _hw()
    sm.on_user_reply_to(hw, _d("W1AW", None, "CQ W1AW"))
    sm.drain_actions()
    sm.on_decodes(hw, [_d("DL1ABC", "DK9XR", "DK9XR DL1ABC -12")])
    assert sm.qso is not None and sm.qso.partner_hat_uns_gerufen is False, \
        "ein Anruf von jemand anderem belegt dieses QSO nicht"
