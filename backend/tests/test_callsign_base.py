"""Tests fuer util.callsign.base_call (v0.27.0)."""

from __future__ import annotations

from ft8_appliance.util.callsign import base_call


def test_plain_call_uppercased():
    assert base_call("dl5abc") == "DL5ABC"
    assert base_call("  DL5ABC  ") == "DL5ABC"


def test_portable_suffixes_stripped():
    assert base_call("DL5ABC/P") == "DL5ABC"
    assert base_call("DL5ABC/M") == "DL5ABC"
    assert base_call("DL5ABC/MM") == "DL5ABC"
    assert base_call("DL5ABC/AM") == "DL5ABC"
    assert base_call("DL5ABC/QRP") == "DL5ABC"


def test_compound_prefix_takes_licensed_call():
    # DL/W1AW → der lizenzierte Call ist W1AW (laengstes Stueck)
    assert base_call("DL/W1AW") == "W1AW"
    assert base_call("W1AW/P") == "W1AW"


def test_hash_resolved_brackets_removed():
    assert base_call("<DL7PM>") == "DL7PM"


def test_empty_and_none():
    assert base_call("") == ""
    assert base_call(None) == ""
