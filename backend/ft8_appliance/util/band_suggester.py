"""Heuristic band-recommendation engine.

Combines:
  * SFI / K-index (current propagation conditions)
  * UTC hour (typical band openings)
  * Recent decode activity per band (empirical "is anything happening?")
  * Distance to target region (skip-zone reasoning)

Returns a sorted list of (band, score, reason). Pure data-only — the
UI decides whether to *suggest* or *enforce* anything. Per Sebastian's
spec: suggestion only, never auto-switch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class BandSuggestion:
    band: str
    score: float        # 0..100 — higher = more recommended
    reason: str


# Per-band time-of-day weighting (UTC hour -> weight 0..1). Very rough
# rules of thumb for mid-latitude HF DX.
_BAND_HOURS: dict[str, dict[range, float]] = {
    "160m": {range(0, 7): 1.0, range(21, 24): 0.8},
    "80m":  {range(0, 8): 1.0, range(20, 24): 0.7},
    "60m":  {range(0, 8): 0.7, range(18, 24): 0.6},
    "40m":  {range(0, 10): 0.8, range(18, 24): 0.8},
    "30m":  {range(0, 24): 0.6},  # 24h band — always something
    "20m":  {range(7, 22): 1.0},
    "17m":  {range(8, 20): 0.9},
    "15m":  {range(9, 18): 0.9},
    "12m":  {range(10, 17): 0.7},
    "10m":  {range(10, 17): 0.7},
    "6m":   {range(11, 19): 0.6},  # Sporadic-E-Saison Sommer
}

# Welche Bänder das Rig physisch kann. Hamlib akzeptiert zwar auch
# außerhalb der Reichweite Setz-Befehle, das Rig sendet aber nicht.
# Hier zählt was sinnvoll vorgeschlagen werden kann.
_RIG_BANDS: dict[str, set[str]] = {
    "ic705":  {"160m","80m","60m","40m","30m","20m","17m","15m","12m","10m","6m","2m","70cm"},
    "ic7300": {"160m","80m","60m","40m","30m","20m","17m","15m","12m","10m","6m"},
    "ic9700": {"2m","70cm","23cm"},
    "ic7610": {"160m","80m","60m","40m","30m","20m","17m","15m","12m","10m","6m"},
}


def _tod_weight(band: str, utc_hour: int) -> float:
    rules = _BAND_HOURS.get(band, {})
    for span, w in rules.items():
        if utc_hour in span:
            return w
    return 0.3


def suggest_bands(
    *,
    utc_hour: int,
    sfi: int | None,
    k_index: int | None,
    decodes_per_band_last_hour: dict[str, int],
    configured_bands: list[str] | None = None,
    rig_model: str | None = None,
    antenna_covers: set[str] | None = None,
) -> list[BandSuggestion]:
    """Return bands sorted by recommendation strength.

    Filterung der Kandidaten:
      * configured_bands → nur Bänder die in der App-Config stehen
      * rig_model → nur was das Rig physisch kann (IC-7300 = HF+6m, etc.)
      * antenna_covers → nur Bänder die mind. eine Antenne abdeckt
    Ist keiner dieser Filter gesetzt, schlagen wir alle Bänder vor die
    wir TOD-Regeln kennen (= grobe Default-Liste).
    """
    out: list[BandSuggestion] = []
    candidates = list(_BAND_HOURS.keys())
    if configured_bands is not None:
        s = set(configured_bands)
        candidates = [b for b in candidates if b in s]
    if rig_model is not None and rig_model in _RIG_BANDS:
        rb = _RIG_BANDS[rig_model]
        candidates = [b for b in candidates if b in rb]
    if antenna_covers is not None:
        candidates = [b for b in candidates if b in antenna_covers]
    sfi_norm = min(1.0, (sfi or 80) / 150.0)
    k_penalty = 1.0 - min(1.0, (k_index or 0) / 6.0)

    for band in candidates:
        tw = _tod_weight(band, utc_hour)
        # Higher bands like SFI > 100, lower bands shrug
        if band in ("12m", "10m", "15m"):
            cond = sfi_norm * k_penalty
        elif band in ("17m", "20m"):
            cond = (0.5 + 0.5 * sfi_norm) * k_penalty
        else:
            cond = 0.8 * k_penalty
        # Activity bonus (clamp 0..1 — log scale would be nicer but YAGNI)
        n = decodes_per_band_last_hour.get(band, 0)
        activity = min(1.0, n / 30.0)

        score = 100 * (0.45 * tw + 0.35 * cond + 0.20 * activity)
        reason_bits: list[str] = []
        if tw > 0.7:
            reason_bits.append("zur Tageszeit gut")
        elif tw < 0.4:
            reason_bits.append("zur Tageszeit schwach")
        if cond > 0.6:
            reason_bits.append(f"SFI {sfi}/K {k_index} günstig")
        elif cond < 0.3:
            reason_bits.append("Bedingungen schwach")
        if activity > 0.4:
            reason_bits.append(f"{n} Decodes letzte Stunde")
        elif n == 0:
            reason_bits.append("tot — keine Decodes letzte Stunde")
        out.append(BandSuggestion(
            band=band, score=score, reason=" · ".join(reason_bits) or "ok",
        ))

    out.sort(key=lambda s: s.score, reverse=True)
    return out
