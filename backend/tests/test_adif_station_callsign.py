"""Audit 2026-07-30: der ADIF-Export schrieb den Heimat-Call in beide
Callsign-Felder.

ADIF trennt sie bewusst: OPERATOR ist die Person, STATION_CALLSIGN das
Rufzeichen, das ueber die Luft ging. Bei Auslandsbetrieb (9A/DK9XR) sind
das verschiedene Werte. Die DB-Spalte dafuer existiert und wird beim
Loggen gefuellt — der Export las sie nur nie. ClubLog- und QRZ-Direkt-
Upload machen es seit v0.22.0 richtig, der manuelle Export nicht: aus
diesem File haetten LotW/eQSL DX-QSOs unter dem falschen Call bekommen.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ft8_appliance.db.models import Qso
from ft8_appliance.web.routes.adif import _render_adif


def _qso(**kw) -> Qso:
    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    base = dict(
        call="W1AW", band="20m", freq_hz=14_074_000, mode="FT8",
        rst_sent=-12, rst_rcvd=-7, qso_start=now, qso_end=now,
        my_grid="JN58ch", user_callsign="DK9XR", station_callsign=None,
    )
    base.update(kw)
    return Qso(**base)


def _fields(adif: str) -> dict[str, str]:
    """<KEY:len>value → dict. Reicht fuer die zwei Felder hier."""
    out = {}
    for part in adif.split("<"):
        if ":" not in part or ">" not in part:
            continue
        head, _, rest = part.partition(">")
        key, _, length = head.partition(":")
        if length.isdigit():
            out[key] = rest[: int(length)]
    return out


def test_dx_operation_exports_the_on_air_callsign() -> None:
    adif = _render_adif([_qso(station_callsign="9A/DK9XR")], "DK9XR", None)
    f = _fields(adif)
    assert f["STATION_CALLSIGN"] == "9A/DK9XR"
    # OPERATOR bleibt die Person, nicht das Luft-Rufzeichen.
    assert f["OPERATOR"] == "DK9XR"


def test_home_operation_keeps_both_fields_equal() -> None:
    adif = _render_adif([_qso(station_callsign=None)], "DK9XR", None)
    f = _fields(adif)
    assert f["STATION_CALLSIGN"] == "DK9XR"
    assert f["OPERATOR"] == "DK9XR"


def test_portable_suffix_is_preserved() -> None:
    adif = _render_adif([_qso(station_callsign="DO3XR/AM")], "DK9XR", None)
    assert _fields(adif)["STATION_CALLSIGN"] == "DO3XR/AM"


def test_foreign_operators_qso_is_not_relabelled_by_the_active_operator() -> None:
    """Im Sammelexport steckt auch das QSO des anderen Operators — der
    aktive Operator darf es nicht ueberschreiben."""
    adif = _render_adif(
        [_qso(user_callsign="DO3XR", station_callsign="DO3XR")], "DK9XR", None
    )
    f = _fields(adif)
    assert f["OPERATOR"] == "DO3XR"
    assert f["STATION_CALLSIGN"] == "DO3XR"
