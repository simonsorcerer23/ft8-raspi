"""DXCC-Rarity-Lookup für Hunt-Picker Tier 8.

Sebastian v0.10.0:
    Lädt ``data/dxcc_rarity.json`` einmalig beim Import und exposed
    eine Lookup-Funktion ``rarity_for(prefix_or_call) -> int`` mit
    Score 0..100 (höher = seltener).

Matching-Strategie:
    1. Exakter Prefix-Match (P5, 3Y/B, etc.) — bevorzugt
    2. Prefix-Reduktion: probiere immer kürzere Prefixe bis Match oder Leer
    3. Default: 0 (common DXCC)

Beispiele:
    rarity_for("P5RYL")  → 100  (Nordkorea, top-rare)
    rarity_for("3Y0J")   → 60   (Bouvet via 3Y0 → fallback auf 3Y prefix-match)
    rarity_for("DK9XR")  → 0    (Deutschland — common)
"""

from __future__ import annotations

import json
import logging
from importlib.resources import files
from typing import Final

log = logging.getLogger(__name__)


def _load_data() -> dict[str, int]:
    """Lädt die JSON-Datei einmalig in einen flachen {prefix: score}-Dict."""
    try:
        raw = (files("ft8_appliance.data") / "dxcc_rarity.json").read_text("utf-8")
        parsed = json.loads(raw)
        entries = parsed.get("entries", {})
        out: dict[str, int] = {}
        for prefix, data in entries.items():
            score = data.get("score") if isinstance(data, dict) else None
            if isinstance(score, int) and 0 <= score <= 100:
                out[prefix.upper()] = score
        log.info("dxcc_rarity: loaded %d entries", len(out))
        return out
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        log.warning("dxcc_rarity: load failed (%s) — all calls treated as common", exc)
        return {}


_RARITY_TABLE: Final[dict[str, int]] = _load_data()


def rarity_for(call_or_prefix: str) -> int:
    """Rarity-Score 0..100 für ein Call oder Prefix. 0 = common.

    Versucht zuerst direkt, dann immer kürzere Prefix-Kandidaten.
    Stop bei Match oder bei Prefix-Länge 1.
    """
    if not call_or_prefix:
        return 0
    key = call_or_prefix.upper().strip()
    # Exact-match probieren
    if key in _RARITY_TABLE:
        return _RARITY_TABLE[key]
    # Prefix-Reduktion: Call wie "P5RYL" → probiere "P5RY", "P5R", "P5", "P"
    # Behalte den höchsten gefundenen Score (eigentlich nur einer wird matchen
    # aber wenn z.B. "FT" und "FT/G" beide drin sind, nehmen wir den
    # spezifischeren — daher prüfen wir lange-zu-kurze Prefixe + nehmen den
    # ersten Treffer)
    n = len(key)
    while n > 1:
        n -= 1
        sub = key[:n]
        if sub in _RARITY_TABLE:
            return _RARITY_TABLE[sub]
    return 0


def is_rare(call_or_prefix: str, threshold: int = 50) -> bool:
    """Convenience: True wenn rarity >= threshold."""
    return rarity_for(call_or_prefix) >= threshold


def all_known_prefixes() -> list[str]:
    """Für Test/Debug — alle Prefixe in der Tabelle."""
    return sorted(_RARITY_TABLE.keys())
