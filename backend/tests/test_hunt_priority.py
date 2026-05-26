"""Tests für die v0.10.0 Hunt-Priority-Tiers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ft8_appliance.integrations.dxcc_rarity import (
    all_known_prefixes,
    is_rare,
    rarity_for,
)
from ft8_appliance.runtime.psk_reciprocity import (
    PskReciprocityCache,
    normalize_call,
)
from ft8_appliance.statemachine.machine import (
    HUNT_TIERS,
    _compute_tier_score,
    _tier_dxcc_rarity,
    _tier_marine,
    _tier_marine_psk,
    _tier_new_dxcc,
    _tier_new_dxcc_band,
    _tier_new_dxcc_psk,
    _tier_not_worked,
    _tier_psk_heard_us,
    _tier_snr,
)
from ft8_appliance.statemachine.states import DecodedMsg, MachineContext


# ---------------------------------------------------------------------------
# DXCC-Rarity Helper
# ---------------------------------------------------------------------------


def test_dxcc_rarity_loaded():
    """JSON-Datei muss da sein und mind. ein paar Prefixe haben."""
    prefixes = all_known_prefixes()
    assert len(prefixes) >= 30, f"erwartete ≥30 Einträge, gefunden {len(prefixes)}"
    assert "P5" in prefixes  # Nordkorea
    assert "3Y/B" in prefixes  # Bouvet


def test_dxcc_rarity_top_entities():
    """Top-3 rarest sollten Score ≥ 95 haben."""
    assert rarity_for("P5RYL") >= 95  # Nordkorea
    assert rarity_for("3Y/B0J") >= 95  # Bouvet — exakt-match
    assert rarity_for("BS7H") >= 95  # Scarborough Reef


def test_dxcc_rarity_common_zero():
    """Standard-EU/US-Calls haben score 0."""
    assert rarity_for("DK9XR") == 0
    assert rarity_for("W1AW") == 0
    assert rarity_for("F5ABC") == 0


def test_dxcc_rarity_prefix_fallback():
    """9J2FI matcht via 9J-prefix-fallback."""
    assert rarity_for("9J2FI") > 0  # Zambia
    assert rarity_for("9JZZZ") > 0  # gleicher prefix


def test_dxcc_rarity_empty():
    """Leerer Call → 0, kein crash."""
    assert rarity_for("") == 0


def test_dxcc_rarity_is_rare_threshold():
    """is_rare mit Default-Threshold."""
    assert is_rare("P5RYL")
    assert not is_rare("DK9XR")
    assert is_rare("9J2FI", threshold=40)  # 9J = 45
    assert not is_rare("9J2FI", threshold=50)  # zu hoch für 9J


# ---------------------------------------------------------------------------
# PSK-Reciprocity Cache
# ---------------------------------------------------------------------------


def test_psk_cache_empty():
    """Frisch erstellter Cache liefert False für jeden Call."""
    cache = PskReciprocityCache()
    assert not cache.heard_us_recently("DK9XR")
    assert not cache.heard_us_recently("KB1MBX")


def test_psk_cache_update_and_lookup():
    """Update setzt entries; recent lookup geht."""
    cache = PskReciprocityCache()
    # Fake HeardReport zum Befüllen
    from ft8_appliance.integrations.psk_reporter import HeardReport
    now = datetime.now(timezone.utc)
    reports = [
        HeardReport(rx_call="KB1MBX", rx_grid="FN42", snr_db=-5, band="20m", mode="FT8", received_at=now),
    ]
    cache.update_from_reports(reports)
    assert cache.heard_us_recently("KB1MBX")
    assert cache.heard_us_recently("kb1mbx")  # case-insensitive
    assert not cache.heard_us_recently("XYZ123")


def test_psk_cache_stale_dropped():
    """Spots älter als freshness_s liefern False."""
    cache = PskReciprocityCache()
    from ft8_appliance.integrations.psk_reporter import HeardReport
    # 1 Stunde alt
    old = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    reports = [HeardReport(rx_call="KB1MBX", rx_grid=None, snr_db=None, band=None, mode=None, received_at=old)]
    cache.update_from_reports(reports)
    # freshness 30 min — Spot ist >30 min alt (relativ zur datetime "now")
    import time as _t
    now_t = _t.time()  # naturally "now" >> old.timestamp()
    assert not cache.heard_us_recently("KB1MBX", now_t=now_t)


def test_psk_normalize_call():
    """Whitespace + lowercase werden normalisiert."""
    assert normalize_call("  dk9xr ") == "DK9XR"
    assert normalize_call("") == ""


# ---------------------------------------------------------------------------
# Tier Functions
# ---------------------------------------------------------------------------


def _decode(call_from, snr=-10, message=None, freq=1500):
    if message is None:
        message = f"CQ {call_from} JN58"
    return DecodedMsg(
        ts=datetime.now(timezone.utc),
        call_from=call_from,
        call_to=None,
        grid=None,
        message=message,
        snr_db=snr,
        dt_s=0.1,
        freq_offset_hz=freq,
        band="15m",
    )


def _ctx(**overrides):
    ctx = MachineContext(callsign="DK9XR", my_grid="JN58", band="15m")
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


def test_tier_marine_hit():
    ctx = _ctx(marine_calls={"DL3QR"})
    assert _tier_marine(_decode("DL3QR"), ctx) == 1
    assert _tier_marine(_decode("EA4XYZ"), ctx) == 0


def test_tier_marine_psk_needs_both():
    """marine_psk hit nur wenn BEIDE Sets matchen."""
    ctx = _ctx(marine_calls={"DL3QR"}, psk_heard_us={"DL3QR"})
    assert _tier_marine_psk(_decode("DL3QR"), ctx) == 1
    ctx2 = _ctx(marine_calls={"DL3QR"}, psk_heard_us={"EA4XYZ"})
    assert _tier_marine_psk(_decode("DL3QR"), ctx2) == 0  # nur marine, kein PSK
    assert _tier_marine_psk(_decode("EA4XYZ"), ctx2) == 0  # nur PSK, kein marine


def test_tier_new_dxcc():
    ctx = _ctx(new_dxcc_calls={"9J2FI"})
    assert _tier_new_dxcc(_decode("9J2FI"), ctx) == 1
    assert _tier_new_dxcc(_decode("DL5ABC"), ctx) == 0


def test_tier_new_dxcc_psk_needs_both():
    ctx = _ctx(new_dxcc_calls={"9J2FI"}, psk_heard_us={"9J2FI"})
    assert _tier_new_dxcc_psk(_decode("9J2FI"), ctx) == 1
    ctx2 = _ctx(new_dxcc_calls={"9J2FI"}, psk_heard_us={"OTHER"})
    assert _tier_new_dxcc_psk(_decode("9J2FI"), ctx2) == 0


def test_tier_psk_heard_us():
    ctx = _ctx(psk_heard_us={"EA4XYZ"})
    assert _tier_psk_heard_us(_decode("EA4XYZ"), ctx) == 1
    assert _tier_psk_heard_us(_decode("OTHER"), ctx) == 0


def test_tier_new_dxcc_band_5bwas():
    """DXCC schon gearbeitet aber NICHT auf diesem Band → tier hit."""
    ctx = _ctx(
        call_to_dxcc={"DL5ABC": "Germany"},
        worked_dxcc_band={("Germany", "20m")},  # nicht 15m
        band="15m",
    )
    # Germany existiert in worked_dxcc_band aber nicht für 15m → score 1
    assert _tier_new_dxcc_band(_decode("DL5ABC"), ctx) == 1
    # Same call after we worked 15m too → score 0
    ctx.worked_dxcc_band.add(("Germany", "15m"))
    assert _tier_new_dxcc_band(_decode("DL5ABC"), ctx) == 0


def test_tier_new_dxcc_band_unknown_entity():
    """Call ohne DXCC-Mapping → 0 (kein Crash)."""
    ctx = _ctx(call_to_dxcc={})
    assert _tier_new_dxcc_band(_decode("DL5ABC"), ctx) == 0


def test_tier_not_worked():
    ctx = _ctx(worked={"DL5ABC"})
    assert _tier_not_worked(_decode("NEW1XYZ"), ctx) == 1
    assert _tier_not_worked(_decode("DL5ABC"), ctx) == 0  # already worked


def test_tier_dxcc_rarity_score():
    ctx = _ctx(rarity_scores={"9J2FI": 45, "P5RYL": 100})
    assert _tier_dxcc_rarity(_decode("9J2FI"), ctx) == 45
    assert _tier_dxcc_rarity(_decode("P5RYL"), ctx) == 100
    assert _tier_dxcc_rarity(_decode("DK9XR"), ctx) == 0


def test_tier_snr():
    """SNR-Tier liefert den Wert direkt, None → -99."""
    ctx = _ctx()
    assert _tier_snr(_decode("X", snr=-5), ctx) == -5
    assert _tier_snr(_decode("X", snr=None), ctx) == -99


# ---------------------------------------------------------------------------
# _compute_tier_score (Aggregation + Ordering)
# ---------------------------------------------------------------------------


def test_compute_score_default_order():
    """Default-Reihenfolge: marine > new_dxcc > psk > worked > rarity > snr."""
    ctx = _ctx(
        hunt_priority=[
            "marine_psk", "marine", "new_dxcc_psk", "new_dxcc",
            "psk_heard_us", "new_dxcc_band", "not_worked", "dxcc_rarity", "snr",
        ],
        marine_calls={"DL3QR"},
        new_dxcc_calls={"9J2FI"},
        psk_heard_us={"EA4XYZ"},
        rarity_scores={"9J2FI": 45},
    )
    s_marine = _compute_tier_score(_decode("DL3QR", -15), ctx)
    s_dxcc = _compute_tier_score(_decode("9J2FI", -20), ctx)
    s_psk = _compute_tier_score(_decode("EA4XYZ", -5), ctx)
    s_common = _compute_tier_score(_decode("DL5ABC", -3), ctx)
    # Marine sollte ALLE schlagen (Tier 2 = marine = 1)
    assert s_marine > s_dxcc
    assert s_marine > s_psk
    assert s_marine > s_common
    # Neu-DXCC schlägt PSK
    assert s_dxcc > s_psk
    # PSK schlägt Common
    assert s_psk > s_common


def test_compute_score_permutation_changes_winner():
    """Umsortieren der hunt_priority ändert den Winner."""
    decodes = [_decode("DL3QR", -15), _decode("EA4XYZ", -5)]
    # Marine zuerst
    ctx1 = _ctx(
        hunt_priority=["marine", "psk_heard_us", "snr"],
        marine_calls={"DL3QR"},
        psk_heard_us={"EA4XYZ"},
    )
    winner1 = max(decodes, key=lambda d: _compute_tier_score(d, ctx1))
    assert winner1.call_from == "DL3QR"
    # PSK zuerst — andere Reihenfolge
    ctx2 = _ctx(
        hunt_priority=["psk_heard_us", "marine", "snr"],
        marine_calls={"DL3QR"},
        psk_heard_us={"EA4XYZ"},
    )
    winner2 = max(decodes, key=lambda d: _compute_tier_score(d, ctx2))
    assert winner2.call_from == "EA4XYZ"


def test_compute_score_unknown_tier_ignored():
    """Tippfehler in Tier-Name darf nicht crashen."""
    ctx = _ctx(
        hunt_priority=["new_dxcc", "NONEXISTENT_TIER", "snr"],
        new_dxcc_calls={"9J2FI"},
    )
    score = _compute_tier_score(_decode("9J2FI"), ctx)
    # Tier "NONEXISTENT_TIER" wird übersprungen, also nur (1, snr)
    assert len(score) == 2
    assert score[0] == 1


def test_compute_score_empty_priority_fallback():
    """Leere hunt_priority → fallback auf reines SNR-Ranking."""
    ctx = _ctx(hunt_priority=[])
    score = _compute_tier_score(_decode("X", -5), ctx)
    # Nur snr (als Fallback hinten)
    assert score == (-5,)


def test_compute_score_snr_tiebreaker_added():
    """Wenn 'snr' nicht in der Liste ist, wird's auto angehängt."""
    ctx = _ctx(hunt_priority=["new_dxcc"], new_dxcc_calls={"9J2FI"})
    s1 = _compute_tier_score(_decode("9J2FI", -10), ctx)
    s2 = _compute_tier_score(_decode("9J2FI", -5), ctx)
    # Beide haben new_dxcc=1 aber unterschiedliches SNR
    assert s1 == (1, -10)
    assert s2 == (1, -5)
    assert s2 > s1


def test_hunt_tiers_registry_complete():
    """Alle 11 erwarteten Tier-Namen sind registriert (v0.10.2: + new_grid, new_grid_band)."""
    expected = {
        "marine_psk", "marine", "new_dxcc_psk", "new_dxcc",
        "psk_heard_us", "new_dxcc_band", "new_grid", "new_grid_band",
        "not_worked", "dxcc_rarity", "snr",
    }
    assert set(HUNT_TIERS.keys()) == expected


def test_tier_new_grid():
    """Decode mit grid, nicht in worked_grids → 1."""
    from ft8_appliance.statemachine.machine import _tier_new_grid
    ctx = _ctx(worked_grids={"JN58"})
    d_new = DecodedMsg(
        ts=datetime.now(timezone.utc), call_from="EA4XYZ", call_to=None,
        grid="IM98", message="CQ EA4XYZ IM98", snr_db=-10, dt_s=0.1,
        freq_offset_hz=1500, band="15m",
    )
    d_known = DecodedMsg(
        ts=datetime.now(timezone.utc), call_from="DL5ABC", call_to=None,
        grid="JN58", message="CQ DL5ABC JN58", snr_db=-10, dt_s=0.1,
        freq_offset_hz=1500, band="15m",
    )
    d_nogrid = DecodedMsg(
        ts=datetime.now(timezone.utc), call_from="W1AW", call_to=None,
        grid=None, message="CQ W1AW", snr_db=-10, dt_s=0.1,
        freq_offset_hz=1500, band="15m",
    )
    assert _tier_new_grid(d_new, ctx) == 1
    assert _tier_new_grid(d_known, ctx) == 0
    assert _tier_new_grid(d_nogrid, ctx) == 0


def test_hunt_priority_auto_migration_adds_missing_tiers():
    """Wenn die User-Config eine alte Liste hat, ergänzt der Validator
    fehlende known-Tiers vor 'snr'."""
    from ft8_appliance.config.models import OperatingConfig
    # Alte v0.10.0 9er-Liste — fehlen new_grid + new_grid_band
    old = [
        "marine_psk", "marine", "new_dxcc_psk", "new_dxcc",
        "psk_heard_us", "new_dxcc_band", "not_worked", "dxcc_rarity", "snr",
    ]
    cfg = OperatingConfig(hunt_priority=old)
    assert "new_grid" in cfg.hunt_priority
    assert "new_grid_band" in cfg.hunt_priority
    # snr bleibt am Ende
    assert cfg.hunt_priority[-1] == "snr"
    # User-Reihenfolge bleibt vorne erhalten
    assert cfg.hunt_priority[0] == "marine_psk"


def test_hunt_priority_auto_migration_preserves_user_order():
    """User-Permutation bleibt erhalten, nur fehlende Tiers werden ergänzt."""
    from ft8_appliance.config.models import OperatingConfig
    # User hat new_dxcc nach oben gezogen
    user = ["new_dxcc", "marine", "snr"]
    cfg = OperatingConfig(hunt_priority=user)
    assert cfg.hunt_priority[0] == "new_dxcc"  # User-Sortierung erhalten
    assert cfg.hunt_priority[1] == "marine"
    assert cfg.hunt_priority[-1] == "snr"
    # alle 11 known Tiers drin
    assert len(cfg.hunt_priority) == 11


def test_hunt_priority_validator_keeps_unknown_tiers():
    """Unbekannte (forward-compat) Tier-Namen werden nicht verworfen."""
    from ft8_appliance.config.models import OperatingConfig
    user = ["FUTURE_TIER_NAME", "marine", "snr"]
    cfg = OperatingConfig(hunt_priority=user)
    assert "FUTURE_TIER_NAME" in cfg.hunt_priority


def test_hunt_priority_validator_empty_list_to_default():
    """Leere Liste in der Config → komplette Default-Liste."""
    from ft8_appliance.config.models import OperatingConfig
    cfg = OperatingConfig(hunt_priority=[])
    assert len(cfg.hunt_priority) == 11
    assert cfg.hunt_priority[0] == "marine_psk"


def test_tier_new_grid_band():
    """VUCC-Band: Grid haben wir, aber nicht auf diesem Band."""
    from ft8_appliance.statemachine.machine import _tier_new_grid_band
    ctx = _ctx(
        band="15m",
        worked_grid_band={("JN58", "20m")},  # JN58 auf 20m schon, aber nicht 15m
    )
    d = DecodedMsg(
        ts=datetime.now(timezone.utc), call_from="DL5ABC", call_to=None,
        grid="JN58", message="CQ DL5ABC JN58", snr_db=-10, dt_s=0.1,
        freq_offset_hz=1500, band="15m",
    )
    assert _tier_new_grid_band(d, ctx) == 1
    # Nach Logging auf 15m:
    ctx.worked_grid_band.add(("JN58", "15m"))
    assert _tier_new_grid_band(d, ctx) == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_no_call_from_returns_zero():
    """DecodedMsg ohne call_from → alle Tiers liefern 0 (kein Crash)."""
    ctx = _ctx()
    d = DecodedMsg(
        ts=datetime.now(timezone.utc), call_from=None, call_to=None, grid=None,
        message="CQ test", snr_db=-10, dt_s=0.1, freq_offset_hz=1500, band="15m",
    )
    for name, fn in HUNT_TIERS.items():
        if name == "snr":
            continue  # snr braucht kein call_from
        assert fn(d, ctx) == 0, f"tier {name} should be 0 for empty call_from"
