"""Marinefunker mf_lookup unit tests."""

import pytest

from ft8_appliance.integrations.mf_lookup import MfLookup, MfMember


@pytest.fixture
def lookup() -> MfLookup:
    return MfLookup(
        data={
            "DK9XR": {"mfnr": 1039, "dok": "T 15", "since": "01.11.2004"},
            "DJ9YD": {"mfnr": 28, "dok": "N09", "since": "01.09.1977"},
            "4K1ADQ": {"mfnr": 494, "dok": "-", "since": "01.10.1989"},
        }
    )


def test_lookup_exact(lookup: MfLookup) -> None:
    m = lookup.lookup("DK9XR")
    assert isinstance(m, MfMember)
    assert m.mfnr == 1039
    assert m.dok == "T 15"


def test_lookup_case_insensitive(lookup: MfLookup) -> None:
    assert lookup.lookup("dk9xr").mfnr == 1039


def test_lookup_strip_suffix_p(lookup: MfLookup) -> None:
    assert lookup.lookup("DK9XR/P").mfnr == 1039


def test_lookup_strip_suffix_mm(lookup: MfLookup) -> None:
    assert lookup.lookup("DK9XR/MM").mfnr == 1039


def test_lookup_strip_brackets(lookup: MfLookup) -> None:
    assert lookup.lookup("<DK9XR>").mfnr == 1039


def test_lookup_compound_prefix_takes_base(lookup: MfLookup) -> None:
    # DL/DK9XR — DK9XR is the licensed base
    assert lookup.lookup("DL/DK9XR").mfnr == 1039


def test_lookup_unknown_returns_none(lookup: MfLookup) -> None:
    assert lookup.lookup("DO3XR") is None


def test_lookup_empty_string(lookup: MfLookup) -> None:
    assert lookup.lookup("") is None


def test_lookup_international_call(lookup: MfLookup) -> None:
    assert lookup.lookup("4K1ADQ").mfnr == 494


def test_contains_operator(lookup: MfLookup) -> None:
    assert "DK9XR" in lookup
    assert "DO3XR" not in lookup


def test_len(lookup: MfLookup) -> None:
    assert len(lookup) == 3
