"""Tests fuer den Blitzortung-WS-Consumer + Storm-Watchdog (v0.13.0)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from ft8_appliance.integrations.blitzortung import BlitzortungClient, Strike
from ft8_appliance.integrations.blitzortung_ws import lzw_decode, parse_strike
from ft8_appliance.integrations.ntfy import NtfyClient


# ---------------------------------------------------------------------------
# LZW-Decoder
# ---------------------------------------------------------------------------


def _lzw_encode_reference(s: str) -> str:
    """Referenz-Encoder spiegelgleich zum Decoder im Modul.

    Wir nutzen das nur um die Round-Trip zu beweisen — der Production-
    Code braucht NUR Decode (Server encoded).
    """
    if not s:
        return ""
    dictionary: dict[str, int] = {chr(i): i for i in range(256)}
    next_code = 256
    out: list[str] = []
    w = ""
    for ch in s:
        wc = w + ch
        if wc in dictionary:
            w = wc
        else:
            out.append(chr(dictionary[w]))
            dictionary[wc] = next_code
            next_code += 1
            w = ch
    if w:
        out.append(chr(dictionary[w]))
    return "".join(out)


def test_lzw_decode_roundtrip_ascii():
    """Standard-Text geht durch encode→decode unveraendert."""
    raw = '{"time":1716800000000000000,"lat":49.50,"lon":11.10}'
    encoded = _lzw_encode_reference(raw)
    assert lzw_decode(encoded) == raw


def test_lzw_decode_empty():
    assert lzw_decode("") == ""


def test_lzw_decode_single_char():
    """Single char = literal, kein Dictionary-Code."""
    encoded = _lzw_encode_reference("X")
    assert lzw_decode(encoded) == "X"


def test_lzw_decode_repetitive_compresses():
    """Wiederholende Pattern werden komprimiert + sauber rekonstruiert."""
    raw = "ABABABABABABAB"
    encoded = _lzw_encode_reference(raw)
    # Encoding muss tatsaechlich kuerzer sein (sonst kein echter LZW)
    assert len(encoded) < len(raw)
    assert lzw_decode(encoded) == raw


# ---------------------------------------------------------------------------
# parse_strike
# ---------------------------------------------------------------------------


def test_parse_strike_valid():
    raw = '{"time":1716800000000000000,"lat":49.50,"lon":11.10,"alt":0}'
    s = parse_strike(raw)
    assert s is not None
    assert abs(s.lat - 49.50) < 1e-9
    assert abs(s.lon - 11.10) < 1e-9
    assert s.ts.tzinfo is UTC


def test_parse_strike_missing_field():
    assert parse_strike('{"lat":49.0}') is None


def test_parse_strike_garbage():
    assert parse_strike("nicht-json") is None
    assert parse_strike("") is None


def test_parse_strike_non_object():
    assert parse_strike("[1,2,3]") is None


# ---------------------------------------------------------------------------
# BlitzortungClient stats
# ---------------------------------------------------------------------------


def test_ingest_updates_stats():
    bz = BlitzortungClient(alarm_radius_km=30)
    assert bz.total_strikes_seen == 0
    assert bz.last_strike_at is None
    now = datetime.now(UTC)
    bz.ingest(Strike(ts=now, lat=49.5, lon=11.1))
    assert bz.total_strikes_seen == 1
    assert bz.last_strike_at == now


def test_ingest_keeps_latest_ts():
    """last_strike_at ist max(strike.ts), nicht zuletzt-eingespeist."""
    bz = BlitzortungClient(alarm_radius_km=30)
    newer = datetime.now(UTC)
    older = newer - timedelta(minutes=5)
    bz.ingest(Strike(ts=newer, lat=49.5, lon=11.1))
    bz.ingest(Strike(ts=older, lat=49.5, lon=11.1))  # backlog/replay
    assert bz.last_strike_at == newer


def test_ingest_disabled_noop():
    bz = BlitzortungClient(alarm_radius_km=30, enabled=False)
    bz.ingest(Strike(ts=datetime.now(UTC), lat=49.5, lon=11.1))
    assert bz.total_strikes_seen == 0


# ---------------------------------------------------------------------------
# Orchestrator-Watchdog: Throttle + Push-Trigger
# ---------------------------------------------------------------------------


class _FakeOrch:
    """Minimaler Stub: nur die Felder + Methode die _blitzortung_check_and_alert
    aus dem Orchestrator braucht."""

    _STORM_THROTTLE_S = 15 * 60
    _STORM_CLOSER_KM = 5.0

    def __init__(self, lat=49.50, lon=11.10):
        from ft8_appliance.runtime.orchestrator import IntegrationContainer
        self.integrations = IntegrationContainer()
        self.integrations.blitzortung = BlitzortungClient(alarm_radius_km=30)
        self.integrations.ntfy = MagicMock(spec=NtfyClient)  # spec → keine Phantom-Methoden
        self.integrations.ntfy.enabled = True
        self.integrations.ntfy.notify = MagicMock()  # not awaited — create_task
        # GPS-Snapshot Fake
        self.gps = MagicMock()
        self.gps.snapshot = MagicMock(lat=lat, lon=lon)
        self._last_storm_alert_at = 0.0
        self._last_storm_alert_km = None

    # Re-use Orchestrator method via bound binding
    def check(self):
        from ft8_appliance.runtime.orchestrator import Orchestrator
        return Orchestrator._blitzortung_check_and_alert(
            self, self.integrations.blitzortung
        )


def test_watchdog_pushes_when_storm_within_radius(monkeypatch):
    """Strike <30km + ntfy enabled → push wird scheduled."""
    # Wir mocken create_task damit der Test nicht in einen Event-Loop muss
    scheduled = []
    monkeypatch.setattr(
        "ft8_appliance.runtime.orchestrator.asyncio.create_task",
        lambda coro, **_: scheduled.append(coro) or MagicMock(),
    )
    fo = _FakeOrch()
    # Strike ~6km von QTH
    fo.integrations.blitzortung.ingest(Strike(
        ts=datetime.now(UTC), lat=49.55, lon=11.10
    ))
    fo.check()
    assert len(scheduled) == 1
    assert fo._last_storm_alert_at > 0
    assert fo._last_storm_alert_km is not None
    assert fo._last_storm_alert_km < 30


def test_watchdog_no_push_when_storm_outside_radius(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        "ft8_appliance.runtime.orchestrator.asyncio.create_task",
        lambda coro, **_: scheduled.append(coro) or MagicMock(),
    )
    fo = _FakeOrch()
    # Strike ~150km von QTH (Muenchen vs Nuernberg)
    fo.integrations.blitzortung.ingest(Strike(
        ts=datetime.now(UTC), lat=48.14, lon=11.58
    ))
    fo.check()
    assert scheduled == []


def test_watchdog_throttle_blocks_repeated_push(monkeypatch):
    """Innerhalb 15min kein zweiter Push fuer aehnlich-weit-entferntes Gewitter."""
    scheduled = []
    monkeypatch.setattr(
        "ft8_appliance.runtime.orchestrator.asyncio.create_task",
        lambda coro, **_: scheduled.append(coro) or MagicMock(),
    )
    fo = _FakeOrch()
    fo.integrations.blitzortung.ingest(Strike(
        ts=datetime.now(UTC), lat=49.55, lon=11.10
    ))
    fo.check()
    assert len(scheduled) == 1
    # Zweiter Strike praktisch gleicher Distanz, kurz danach
    fo.integrations.blitzortung.ingest(Strike(
        ts=datetime.now(UTC), lat=49.55, lon=11.10
    ))
    fo.check()
    assert len(scheduled) == 1, "Throttle muss zweiten Push blocken"


def test_watchdog_re_pushes_when_storm_closer(monkeypatch):
    """Innerhalb des Throttles aber >=5km naeher → re-push erlaubt."""
    scheduled = []
    monkeypatch.setattr(
        "ft8_appliance.runtime.orchestrator.asyncio.create_task",
        lambda coro, **_: scheduled.append(coro) or MagicMock(),
    )
    fo = _FakeOrch(lat=49.50, lon=11.10)
    # Erst weit (~20km)
    fo.integrations.blitzortung.ingest(Strike(
        ts=datetime.now(UTC), lat=49.68, lon=11.10
    ))
    fo.check()
    assert len(scheduled) == 1
    first_km = fo._last_storm_alert_km
    # Dann nah (~3km) — deutlich naeher als first_km
    fo.integrations.blitzortung.ingest(Strike(
        ts=datetime.now(UTC), lat=49.527, lon=11.10
    ))
    fo.check()
    assert len(scheduled) == 2
    assert fo._last_storm_alert_km < first_km - 5.0


def test_watchdog_skips_when_no_gps(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        "ft8_appliance.runtime.orchestrator.asyncio.create_task",
        lambda coro, **_: scheduled.append(coro) or MagicMock(),
    )
    fo = _FakeOrch(lat=None, lon=None)
    fo.integrations.blitzortung.ingest(Strike(
        ts=datetime.now(UTC), lat=49.55, lon=11.10
    ))
    fo.check()
    assert scheduled == []


def test_watchdog_skips_when_ntfy_disabled(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        "ft8_appliance.runtime.orchestrator.asyncio.create_task",
        lambda coro, **_: scheduled.append(coro) or MagicMock(),
    )
    fo = _FakeOrch()
    fo.integrations.ntfy.enabled = False
    fo.integrations.blitzortung.ingest(Strike(
        ts=datetime.now(UTC), lat=49.55, lon=11.10
    ))
    fo.check()
    assert scheduled == []
