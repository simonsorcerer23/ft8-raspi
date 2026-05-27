"""Tests v0.20.0 — UI-Cleanup (Hints raus) + Directed-CQ aufs Dashboard."""

from __future__ import annotations

import re

import pytest


# Normalisierung (im endpoint-Handler dupliziert via Inline-Regex)
def _normalize_cq_directed(raw: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())[:4]


def test_normalize_strip_special_chars():
    assert _normalize_cq_directed("d-x") == "DX"
    assert _normalize_cq_directed("dx!") == "DX"


def test_normalize_uppercases():
    assert _normalize_cq_directed("dx") == "DX"
    assert _normalize_cq_directed("Pota") == "POTA"


def test_normalize_max_4_chars():
    assert _normalize_cq_directed("WPXSS") == "WPXS"
    assert _normalize_cq_directed("longstring") == "LONG"


def test_normalize_empty():
    assert _normalize_cq_directed("") == ""
    assert _normalize_cq_directed(None) == ""


def test_normalize_only_special_chars_returns_empty():
    assert _normalize_cq_directed("!!!") == ""
    assert _normalize_cq_directed("  ") == ""
