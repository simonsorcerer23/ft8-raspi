"""Parsing helpers for nmcli terse output.

The integration with the live nmcli binary is exercised manually on the
Pi — here we only pin the escape-handling logic so future refactors
don't silently break SSIDs that contain ``:``.
"""

from __future__ import annotations

from ft8_appliance.util.network import _split_nmcli_terse


def test_plain_split() -> None:
    assert _split_nmcli_terse("foo:bar:baz", expected=3) == ["foo", "bar", "baz"]


def test_pads_to_expected_when_short() -> None:
    assert _split_nmcli_terse("a:b", expected=4) == ["a", "b", "", ""]


def test_extra_fields_kept() -> None:
    assert _split_nmcli_terse("a:b:c:d:e", expected=3) == ["a", "b", "c", "d", "e"]


def test_escaped_colon_inside_field() -> None:
    # nmcli emits "Foo\:Bar:other" for SSID "Foo:Bar" in column 1.
    parts = _split_nmcli_terse(r"Foo\:Bar:other", expected=2)
    assert parts == ["Foo:Bar", "other"]


def test_multiple_escaped_colons() -> None:
    parts = _split_nmcli_terse(r"a\:b\:c:d:e", expected=3)
    assert parts == ["a:b:c", "d", "e"]


def test_empty_field() -> None:
    assert _split_nmcli_terse("a::c", expected=3) == ["a", "", "c"]
