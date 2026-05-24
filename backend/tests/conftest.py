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
