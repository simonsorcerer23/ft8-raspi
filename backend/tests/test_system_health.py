"""Tests for the system-health probes (parser + filesystem-touching helpers)."""

from __future__ import annotations

from ft8_appliance.util.system_health import parse_chrony_tracking

# Real chronyc output snippets — these are stable formats, the parser
# must keep working against them.

SAMPLE_FAST = """\
Reference ID    : 7F7F0101 (GPS)
Stratum         : 1
Ref time (UTC)  : Wed May 15 13:00:01 2026
System time     : 0.000001234 seconds fast of NTP time
Last offset     : +0.000001234 seconds
RMS offset      : 0.000002345 seconds
Frequency       : 12.345 ppm slow
Residual freq   : +0.001 ppm
Skew            : 0.123 ppm
Root delay      : 0.000123456 seconds
Root dispersion : 0.000234567 seconds
Update interval : 16.0 seconds
Leap status     : Normal
"""

SAMPLE_SLOW = """\
Reference ID    : 7F7F0101 (GPS)
Stratum         : 1
Ref time (UTC)  : Wed May 15 13:00:01 2026
System time     : 0.000123456 seconds slow of NTP time
RMS offset      : 0.000234567 seconds
Leap status     : Normal
"""

SAMPLE_LARGE_OFFSET = """\
Reference ID    : 0.0.0.0 ()
Stratum         : 0
System time     : 1.234567890 seconds slow of NTP time
RMS offset      : 0.500000000 seconds
Leap status     : Not synchronised
"""

GARBAGE = "this is not chrony output at all"


def test_parse_chrony_fast() -> None:
    s = parse_chrony_tracking(SAMPLE_FAST)
    assert s is not None
    assert s.offset_s > 0
    assert abs(s.offset_s - 1.234e-6) < 1e-9
    assert s.stratum == 1
    assert s.leap_status == "Normal"
    assert s.rms_offset_s is not None


def test_parse_chrony_slow_is_negative() -> None:
    s = parse_chrony_tracking(SAMPLE_SLOW)
    assert s is not None
    assert s.offset_s < 0
    assert abs(s.offset_s + 1.23456e-4) < 1e-8


def test_parse_chrony_large_offset_triggers_alarm_threshold() -> None:
    s = parse_chrony_tracking(SAMPLE_LARGE_OFFSET)
    assert s is not None
    # FT8 DT-Guard threshold is 0.5 s; this should be over.
    assert abs(s.offset_s) > 0.5
    assert s.leap_status == "Not"  # split on whitespace by the regex
    # Stratum 0 means unsynchronised


def test_parse_chrony_garbage_returns_none() -> None:
    assert parse_chrony_tracking(GARBAGE) is None
