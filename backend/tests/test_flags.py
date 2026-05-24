"""Tests fuer flags.py — DXCC → ISO2 → Flag-Emoji."""

from __future__ import annotations

from ft8_appliance.integrations.cty_dat import CtyDat, DxccEntity, DxccLookupResult
from ft8_appliance.integrations.flags import (
    flag_for_call,
    iso2_for_dxcc_primary,
    iso2_to_flag,
)


# ----------------------------------------------------------- iso2_to_flag


def test_iso2_to_flag_germany() -> None:
    assert iso2_to_flag("DE") == "🇩🇪"


def test_iso2_to_flag_united_states() -> None:
    assert iso2_to_flag("US") == "🇺🇸"


def test_iso2_to_flag_japan() -> None:
    assert iso2_to_flag("JP") == "🇯🇵"


def test_iso2_to_flag_lowercase_works() -> None:
    assert iso2_to_flag("de") == "🇩🇪"


def test_iso2_to_flag_invalid_returns_empty() -> None:
    assert iso2_to_flag("") == ""
    assert iso2_to_flag("D") == ""
    assert iso2_to_flag("DEU") == ""
    assert iso2_to_flag("D1") == ""
    assert iso2_to_flag("Doppelt") == ""


# ----------------------------------------------------------- mapping table


def test_top_dxcc_entities_mapped() -> None:
    """Die wichtigsten DXCC-Entities haben einen Eintrag."""
    must_have = {
        "DL": "DE", "K": "US", "JA": "JP", "G": "GB", "F": "FR",
        "I": "IT", "EA": "ES", "OK": "CZ", "SP": "PL", "OH": "FI",
        "SM": "SE", "OE": "AT", "PA": "NL", "ON": "BE", "EI": "IE",
        "VK": "AU", "ZL": "NZ", "VE": "CA", "LU": "AR", "PY": "BR",
        "UR": "UA", "R": "RU", "BY": "CN", "HL": "KR", "VU": "IN",
        "LA": "NO", "OZ": "DK", "9A": "HR", "LZ": "BG", "YO": "RO",
        "S5": "SI", "OM": "SK", "HA": "HU", "LY": "LT", "YL": "LV",
        "ES": "EE", "EU": "BY", "ER": "MD", "TA": "TR", "YU": "RS",
        "Z3": "MK", "ZA": "AL", "E7": "BA", "4O": "ME", "SV": "GR",
    }
    for prefix, expected_iso in must_have.items():
        assert iso2_for_dxcc_primary(prefix) == expected_iso, \
            f"{prefix} should map to {expected_iso}"


def test_unknown_prefix_returns_none() -> None:
    assert iso2_for_dxcc_primary("XXX") is None
    assert iso2_for_dxcc_primary("") is None


def test_skip_list_returns_none() -> None:
    """Antarktis / ITU / UN-Sonder-DXCC ohne Flagge → None."""
    assert iso2_for_dxcc_primary("CE9") is None
    assert iso2_for_dxcc_primary("KC4") is None
    assert iso2_for_dxcc_primary("1A") is None


def test_prefix_fallback_works() -> None:
    """Wenn cty.dat irgendwann einen längeren Prefix liefert als wir
    haben (z.B. 'KH6A' statt 'KH6'), fallen wir defensiv zurück."""
    # KH6A nicht in Tabelle, KH6 ist drin → KH6 (=US Hawaii)
    assert iso2_for_dxcc_primary("KH6A") == "US"
    # 9M2A nicht in Tabelle, 9M2 ist drin
    assert iso2_for_dxcc_primary("9M2A") == "MY"


# ----------------------------------------------------------- flag_for_call


def _mini_cty() -> CtyDat:
    """Mini-cty.dat-String mit den Test-Entities."""
    src = (
        "Germany:                14: 28: EU:51.00:    10.00:    -1.0:  DL:\n"
        "DA,DB,DC,DD,DE,DF,DG,DH,DI,DJ,DK,DL,DM,DN,DO,DP,DQ,DR;\n"
        "United States:           5:  8: NA:38.00:    98.00:     5.0:  K:\n"
        "AA,AB,AC,AD,AE,AF,AG,AI,AJ,AK,K,N,W;\n"
        "Japan:                  25: 45: AS:36.00:  -138.00:    -9.0:  JA:\n"
        "JA,JE,JF,JG,JH,JI,JJ,JK,JL,JM,JN,JO,JP,JQ,JR,JS,7J,7K,7L,7M,7N,8J,8N;\n"
        "Spain:                  14: 37: EU:40.00:     4.00:    -1.0:  EA:\n"
        "EA,EB,EC,ED,EE,EF,EG,EH;\n"
    )
    return CtyDat.from_string(src)


def test_flag_for_call_dk_callsign() -> None:
    cty = _mini_cty()
    assert flag_for_call("DK9XR", cty) == "🇩🇪"
    assert flag_for_call("DL5XYZ", cty) == "🇩🇪"
    assert flag_for_call("DO3XR", cty) == "🇩🇪"


def test_flag_for_call_us_callsign() -> None:
    cty = _mini_cty()
    assert flag_for_call("W1AW", cty) == "🇺🇸"
    assert flag_for_call("K1JT", cty) == "🇺🇸"
    assert flag_for_call("N4RUF", cty) == "🇺🇸"


def test_flag_for_call_japanese_callsign() -> None:
    cty = _mini_cty()
    assert flag_for_call("JA1ABC", cty) == "🇯🇵"


def test_flag_for_call_spanish_callsign() -> None:
    cty = _mini_cty()
    assert flag_for_call("EA1AKS", cty) == "🇪🇸"


def test_flag_for_call_lowercase_works() -> None:
    """CtyDat normalisiert intern auf upper, sollte auch lower akzeptieren."""
    cty = _mini_cty()
    assert flag_for_call("dl1abc", cty) == "🇩🇪"


def test_flag_for_call_unknown_call_returns_empty() -> None:
    cty = _mini_cty()
    # XYZ ist in keiner Prefix-Liste der Mini-Cty
    # → leerer String, kein crash
    assert flag_for_call("XYZ123", cty) == ""


def test_flag_for_call_none_inputs_safe() -> None:
    cty = _mini_cty()
    assert flag_for_call(None, cty) == ""
    assert flag_for_call("DL1ABC", None) == ""
    assert flag_for_call("", cty) == ""


def test_flag_for_call_handles_lookup_exception() -> None:
    """Wenn der ctydb.lookup wirft (z.B. internal-Inkonsistenz), nicht
    crashen — leerer String zurueck."""
    class BrokenCty:
        def lookup(self, call):
            raise RuntimeError("broken")
    assert flag_for_call("DL1ABC", BrokenCty()) == ""
