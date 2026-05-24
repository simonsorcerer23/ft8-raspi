"""Amateur radio band lookup + IARU bandplan FT8-segment helpers.

Band ranges per IARU region (R1=EU/AF, R2=NA/SA, R3=AS/OC) for the
FT8 standard frequencies. Region resolution is GPS-based (rough
lat/lon bucketing — close enough for portable operation).
"""

from __future__ import annotations

from dataclasses import dataclass

# (lo_hz, hi_hz, name) — covers HF + VHF/UHF for completeness
_BANDS = (
    (1_800_000,    2_000_000,   "160m"),
    (3_500_000,    4_000_000,   "80m"),
    (5_330_000,    5_410_000,   "60m"),
    (7_000_000,    7_300_000,   "40m"),
    (10_100_000,  10_150_000,   "30m"),
    (14_000_000,  14_350_000,   "20m"),
    (18_068_000,  18_168_000,   "17m"),
    (21_000_000,  21_450_000,   "15m"),
    (24_890_000,  24_990_000,   "12m"),
    (28_000_000,  29_700_000,   "10m"),
    (50_000_000,  54_000_000,   "6m"),
    (144_000_000, 148_000_000,  "2m"),
    (430_000_000, 450_000_000,  "70cm"),
)


def band_from_freq_hz(hz: int) -> str | None:
    """Return e.g. "20m" for 14_074_000 Hz, or None if out-of-band."""
    for lo, hi, name in _BANDS:
        if lo <= hz <= hi:
            return name
    return None


# ---------------------------------------------------------------------------
# IARU FT8 segments per region. Source: IARU bandplans 2025.
# Outside these windows TX is illegal in that region.
@dataclass(frozen=True, slots=True)
class FT8Segment:
    band: str
    region: int   # 1, 2 or 3
    lo_hz: int
    hi_hz: int


_FT8_SEGMENTS: tuple[FT8Segment, ...] = (
    # 160m  — narrow, region differences
    FT8Segment("160m", 1, 1_840_000, 1_842_000),
    FT8Segment("160m", 2, 1_840_000, 1_842_000),
    FT8Segment("160m", 3, 1_840_000, 1_842_000),
    # 80m
    FT8Segment("80m",  1, 3_573_000, 3_575_000),
    FT8Segment("80m",  2, 3_573_000, 3_575_000),
    FT8Segment("80m",  3, 3_573_000, 3_575_000),
    # 60m — R1 only specific channels
    FT8Segment("60m",  2, 5_357_000, 5_360_000),
    # 40m
    FT8Segment("40m",  1, 7_074_000, 7_076_000),
    FT8Segment("40m",  2, 7_074_000, 7_076_000),
    FT8Segment("40m",  3, 7_074_000, 7_076_000),
    # 30m
    FT8Segment("30m",  1, 10_136_000, 10_138_000),
    FT8Segment("30m",  2, 10_136_000, 10_138_000),
    FT8Segment("30m",  3, 10_136_000, 10_138_000),
    # 20m
    FT8Segment("20m",  1, 14_074_000, 14_076_000),
    FT8Segment("20m",  2, 14_074_000, 14_076_000),
    FT8Segment("20m",  3, 14_074_000, 14_076_000),
    # 17m
    FT8Segment("17m",  1, 18_100_000, 18_102_000),
    FT8Segment("17m",  2, 18_100_000, 18_102_000),
    FT8Segment("17m",  3, 18_100_000, 18_102_000),
    # 15m
    FT8Segment("15m",  1, 21_074_000, 21_076_000),
    FT8Segment("15m",  2, 21_074_000, 21_076_000),
    FT8Segment("15m",  3, 21_074_000, 21_076_000),
    # 12m
    FT8Segment("12m",  1, 24_915_000, 24_917_000),
    FT8Segment("12m",  2, 24_915_000, 24_917_000),
    FT8Segment("12m",  3, 24_915_000, 24_917_000),
    # 10m
    FT8Segment("10m",  1, 28_074_000, 28_076_000),
    FT8Segment("10m",  2, 28_074_000, 28_076_000),
    FT8Segment("10m",  3, 28_074_000, 28_076_000),
    # 6m
    FT8Segment("6m",   1, 50_313_000, 50_315_000),
    FT8Segment("6m",   2, 50_313_000, 50_315_000),
    FT8Segment("6m",   3, 50_313_000, 50_315_000),
    # 2m
    FT8Segment("2m",   1, 144_174_000, 144_176_000),
    FT8Segment("2m",   2, 144_174_000, 144_176_000),
    FT8Segment("2m",   3, 144_174_000, 144_176_000),
    # 70cm
    FT8Segment("70cm", 1, 432_174_000, 432_176_000),
    FT8Segment("70cm", 2, 432_174_000, 432_176_000),
    FT8Segment("70cm", 3, 432_174_000, 432_176_000),
)


def iaru_region_from_latlon(lat: float | None, lon: float | None) -> int | None:
    """Map (lat, lon) to IARU region 1/2/3.

    Simplified rules — fine for portable operation:
      Region 1: Europe, Africa, Middle East, Northern Asia (Russia west)
      Region 2: Americas
      Region 3: Asia/Pacific
    """
    if lat is None or lon is None:
        return None
    if -170 <= lon <= -30:  # Americas
        return 2
    if -30 < lon < 60:      # EU/AF
        return 1
    return 3                # Asia/Pacific


def is_in_ft8_segment(band: str, freq_hz: int, region: int) -> bool:
    """True if *freq_hz* is inside the IARU FT8 sub-band for *region*."""
    for seg in _FT8_SEGMENTS:
        if seg.band == band and seg.region == region:
            return seg.lo_hz <= freq_hz <= seg.hi_hz
    # If we have no rule for this band/region, default to allowing —
    # better than blocking valid operation on a rule we don't know.
    return True
