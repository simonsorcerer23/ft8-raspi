"""Tests fuer NG3K ADXO Parser (v0.19.1)."""

from __future__ import annotations

from datetime import UTC

import pytest

from ft8_appliance.integrations.dxped_ng3k import (
    DxpedEntry,
    _parse_date,
    _strip_html,
    parse_ng3k_html,
)


# ---------------------------------------------------------------------------
# Date Parsing
# ---------------------------------------------------------------------------


def test_parse_date_compact_format():
    """NG3K-Standard: '2026 Mar25' ohne Space zwischen Mar und 25."""
    d = _parse_date("2026 Mar25")
    assert d is not None
    assert d.year == 2026
    assert d.month == 3
    assert d.day == 25
    assert d.tzinfo == UTC


def test_parse_date_with_space():
    """Alternative: '2026 Mar 25' mit Space."""
    d = _parse_date("2026 Mar 25")
    assert d is not None
    assert d.month == 3
    assert d.day == 25


def test_parse_date_invalid_returns_none():
    assert _parse_date("garbage") is None
    assert _parse_date("") is None
    assert _parse_date("2026 Mar32") is None  # day out of range


# ---------------------------------------------------------------------------
# HTML Stripping
# ---------------------------------------------------------------------------


def test_strip_html_basic():
    assert _strip_html("By <a href='x'>LU5DX</a>; HF") == "By LU5DX; HF"


def test_strip_html_entities():
    assert _strip_html("ab&amp;cd") == "ab&cd"
    assert _strip_html("a&nbsp;b") == "a b"


def test_strip_html_collapse_whitespace():
    assert _strip_html("a\n  b\t\tc") == "a b c"


# ---------------------------------------------------------------------------
# Full Parser
# ---------------------------------------------------------------------------


SAMPLE_HTML = """
<html>
<body>
<table>
<tr class="adxoitem" bgcolor="#FFDAB9">
  <td class="date">2026 Mar25</td>
  <td class="date">2026 May31</td>
  <td class="cty">Galapagos</td>
  <td><span class="call">HD8R</span></td>
  <td class="qsl">M0OXO</td>
  <td class="rep">TDDX</td>
  <td class="info">By LU5DX; HF; QRV for CQ WPX SSB</td>
</tr>
<tr class="adxoitem" bgcolor="#FFDAB9">
  <td class="date">2026 Jun20</td>
  <td class="date">2026 Jul05</td>
  <td class="cty">Spratly Islands</td>
  <td><span class="call">9M0AXA</span></td>
  <td class="qsl">ClubLog OQRS</td>
  <td class="rep">DX-World</td>
  <td class="info">By multi-op team; 80-10m; CW SSB FT8</td>
</tr>
<tr class="adxoitem">
  <td class="date">garbage</td>
  <td class="date">2026 Aug01</td>
  <td class="cty">Mars</td>
  <td><span class="call">M4RS</span></td>
  <td class="info">should be skipped</td>
</tr>
</table>
</body>
</html>
"""


def test_parse_ng3k_extracts_entries():
    entries = parse_ng3k_html(SAMPLE_HTML)
    assert len(entries) == 2  # garbage-Zeile wird uebersprungen
    e1, e2 = entries
    assert e1.call == "HD8R"
    assert e1.dxcc_name == "Galapagos"
    assert e1.start.month == 3 and e1.start.day == 25
    assert e1.end.month == 5 and e1.end.day == 31
    assert "LU5DX" in e1.info
    assert e2.call == "9M0AXA"
    assert e2.dxcc_name == "Spratly Islands"


def test_parse_ng3k_skips_call_too_long():
    """Calls > 13 Chars (kein FT8-valides Call) werden uebersprungen."""
    html = """
    <tr class="adxoitem">
      <td class="date">2026 Mar25</td>
      <td class="date">2026 May31</td>
      <td class="cty">Test</td>
      <td><span class="call">THIS_IS_TOO_LONG_FOR_FT8</span></td>
      <td class="info">x</td>
    </tr>
    """
    assert parse_ng3k_html(html) == []


def test_parse_ng3k_empty_html():
    assert parse_ng3k_html("") == []
    assert parse_ng3k_html("<html><body>no tr</body></html>") == []


def test_parse_ng3k_call_uppercased():
    html = """
    <tr class="adxoitem">
      <td class="date">2026 Mar25</td>
      <td class="date">2026 May31</td>
      <td class="cty">X</td>
      <td><span class="call">fo/f6bcw</span></td>
      <td class="info">x</td>
    </tr>
    """
    entries = parse_ng3k_html(html)
    assert len(entries) == 1
    assert entries[0].call == "FO/F6BCW"


def test_parse_ng3k_end_before_start_skipped():
    """Plausibility-Check: end < start → skip."""
    html = """
    <tr class="adxoitem">
      <td class="date">2026 May31</td>
      <td class="date">2026 Mar25</td>
      <td class="cty">X</td>
      <td><span class="call">XX1Y</span></td>
      <td class="info">x</td>
    </tr>
    """
    assert parse_ng3k_html(html) == []


def test_parse_ng3k_info_truncated_at_200():
    long_info = "A" * 300
    html = f"""
    <tr class="adxoitem">
      <td class="date">2026 Mar25</td>
      <td class="date">2026 May31</td>
      <td class="cty">X</td>
      <td><span class="call">XX1Y</span></td>
      <td class="info">{long_info}</td>
    </tr>
    """
    entries = parse_ng3k_html(html)
    assert len(entries) == 1
    assert len(entries[0].info) <= 200
    assert entries[0].info.endswith("...")
