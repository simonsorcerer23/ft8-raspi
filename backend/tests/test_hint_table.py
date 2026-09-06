"""Known-Call-Tabelle des Shims (2026-09-06).

ft8_shim_hash_table_save(call, 0) — der Weg, auf dem Orchestrator und
Benchmarks die Tabelle fuellen — landete immer im selben Slot, weil die
Dedupe auf n22 == 0 den ersten Eintrag traf. Alle Feeds kollabierten auf
EINEN bekannten Call.
"""

from __future__ import annotations

from ft8_appliance.decode.ft8_native import lib


def test_python_side_saves_get_distinct_entries() -> None:
    before = lib.ft8_shim_hash_table_count()
    calls = [f"DL{i}ZZZ" for i in range(1, 8)]
    for c in calls:
        assert lib.ft8_shim_hash_table_save(c.encode(), 0) > 0
    assert lib.ft8_shim_hash_table_count() >= before + len(calls)
    # zweimal derselbe Call: kein zweiter Eintrag
    n = lib.ft8_shim_hash_table_count()
    lib.ft8_shim_hash_table_save(b"DL1ZZZ", 0)
    assert lib.ft8_shim_hash_table_count() == n


def test_invalid_characters_are_rejected() -> None:
    assert lib.ft8_shim_hash_table_save(b"DL1?ZZ", 0) == -1
