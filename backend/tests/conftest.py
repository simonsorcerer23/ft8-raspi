"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
TEST_DATA_DIR = TESTS_DIR / "data"


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    """Directory with sample WAV files etc. used by tests."""
    return TEST_DATA_DIR


@pytest.fixture(scope="session")
def ft8_lib_root() -> Path:
    """Path to the vendored ft8_lib submodule."""
    return Path(__file__).parents[2] / "vendor" / "ft8_lib"


@pytest.fixture(autouse=True)
def synced_clock(monkeypatch: pytest.MonkeyPatch):
    """Jedem Test eine synchrone Uhr geben, ohne chronyd auf der Maschine.

    Seit 2026-09-06 (Audit A1) vertraut der time_guard nur noch chrony.
    ``read_chrony_tracking`` shellt zu ``chronyc`` aus und liefert None,
    wo das nicht installiert ist (Dev-Rechner) — jeder Orchestrator-Test
    liefe dann in TX_LOCKED "Uhr nicht synchron". Vorher trug der GPS-Mock
    diese Tests durch, und genau das war die Luecke: ein GPS-Fix stellt
    die Uhr nicht.

    Tests, die Zeitverhalten pruefen, ueberschreiben das gezielt
    (``_force_chrony`` in test_chaos / test_wave1_guards) — ein spaeterer
    ``monkeypatch.setattr`` im Testkoerper gewinnt gegen diese Fixture.
    """
    from ft8_appliance.runtime import orchestrator as orchestrator_mod
    from ft8_appliance.util.system_health import ChronyStatus

    async def _synced() -> ChronyStatus:
        return ChronyStatus(offset_s=0.001, stratum=3, ref_id_name="test-ntp")

    monkeypatch.setattr(orchestrator_mod, "read_chrony_tracking", _synced)
