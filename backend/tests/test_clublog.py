"""Tests v0.21.0 — ClubLog Real-Time-Upload-Integration."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from ft8_appliance.config.models import OperatorConfig
from ft8_appliance.db.models import Qso
from ft8_appliance.integrations.clublog import (
    CLUBLOG_BULK_URL,
    CLUBLOG_URL,
    ClubLogError,
    _qso_to_adif,
    _qsos_to_adif,
    bulk_upload,
    upload_qso,
)


def _make_qso(**overrides) -> Qso:
    base = dict(
        call="EA5KB",
        band="15m",
        freq_hz=21_074_000,
        mode="FT8",
        rst_sent=-10,
        rst_rcvd=-15,
        grid_rcvd="IM99",
        qso_start=datetime(2026, 5, 27, 18, 30, 45, tzinfo=UTC),
        qso_end=datetime(2026, 5, 27, 18, 31, 15, tzinfo=UTC),
        my_grid="JN58",
        my_power_w=50,
        notes=None,
    )
    base.update(overrides)
    q = Qso(**base)
    return q


# ---------------------------------------------------------------------------
# ADIF rendering
# ---------------------------------------------------------------------------


def test_adif_basic_fields():
    q = _make_qso()
    adif = _qso_to_adif(q, "DO3XR")
    assert "<call:5>EA5KB" in adif
    assert "<qso_date:8>20260527" in adif
    assert "<time_on:6>183045" in adif
    assert "<band:3>15m" in adif
    assert "<freq:7>21.0740" in adif
    assert "<mode:3>FT8" in adif
    assert "<station_callsign:5>DO3XR" in adif
    assert "<operator:5>DO3XR" in adif
    assert "<rst_sent:3>-10" in adif
    assert "<rst_rcvd:3>-15" in adif
    assert "<gridsquare:4>IM99" in adif
    assert "<my_gridsquare:4>JN58" in adif
    assert "<tx_pwr:2>50" in adif
    assert adif.endswith("<eor>")


def test_adif_skips_none_fields():
    q = _make_qso(rst_sent=None, rst_rcvd=None, grid_rcvd=None, notes=None)
    adif = _qso_to_adif(q, "DO3XR")
    assert "<rst_sent:" not in adif
    assert "<rst_rcvd:" not in adif
    assert "<gridsquare:" not in adif
    assert "<comment:" not in adif


def test_adif_includes_notes():
    q = _make_qso(notes="73 + new DXCC")
    adif = _qso_to_adif(q, "DO3XR")
    assert "<comment:13>73 + new DXCC" in adif


# ---------------------------------------------------------------------------
# upload_qso — mocked HTTP via httpx.MockTransport
# ---------------------------------------------------------------------------


def _patch_httpx(monkeypatch, handler):
    """Patch httpx.AsyncClient so requests gehen an handler statt ins Netz."""
    transport = httpx.MockTransport(handler)
    real_init = httpx.AsyncClient.__init__

    def fake_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", fake_init)


async def test_upload_ok_empty_body(monkeypatch):
    """ClubLog antwortet typisch mit HTTP 200 + leerem Body bei Erfolg."""
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = req.content.decode()
        return httpx.Response(200, text="")

    _patch_httpx(monkeypatch, handler)
    q = _make_qso()
    await upload_qso("me@example.com", "secret-app-pw", "deadbeef-api-key", "DO3XR", q)
    assert seen["url"] == CLUBLOG_URL
    assert "email=me%40example.com" in seen["body"]
    assert "password=secret-app-pw" in seen["body"]
    assert "callsign=DO3XR" in seen["body"]
    assert "api=deadbeef-api-key" in seen["body"]  # v0.21.1: api-Key mitgesendet
    assert "EA5KB" in seen["body"]  # ADIF im Body


async def test_upload_ok_qso_ok_body(monkeypatch):
    """ClubLog-Doku-konformes "QSO OK" → Erfolg."""
    def handler(req):
        return httpx.Response(200, text="QSO OK")
    _patch_httpx(monkeypatch, handler)
    await upload_qso("me@example.com", "pw", "key", "DO3XR", _make_qso())


async def test_upload_ok_qso_duplicate_body(monkeypatch):
    """QSO Duplicate ist auch ein Erfolg (idempotent)."""
    def handler(req):
        return httpx.Response(200, text="QSO Duplicate, no changes")
    _patch_httpx(monkeypatch, handler)
    await upload_qso("me@example.com", "pw", "key", "DO3XR", _make_qso())


async def test_upload_ok_qso_modified_body(monkeypatch):
    """QSO Modified: ClubLog hat Korrekturen — auch Erfolg."""
    def handler(req):
        return httpx.Response(200, text="QSO Modified (DXCC resolved)")
    _patch_httpx(monkeypatch, handler)
    await upload_qso("me@example.com", "pw", "key", "DO3XR", _make_qso())


async def test_upload_ok_bare_ok_body(monkeypatch):
    """Live-Beobachtung 2026-05-28: ClubLog antwortet typisch mit bare
    'OK' (kein "QSO "-Prefix). v0.21.3-Fix."""
    def handler(req):
        return httpx.Response(200, text="OK")
    _patch_httpx(monkeypatch, handler)
    await upload_qso("me@example.com", "pw", "key", "DO3XR", _make_qso())


async def test_upload_ok_updated_qso_body(monkeypatch):
    """Live-Beobachtung 2026-05-28: 'Updated QSO' = ClubLog hat einen
    schon vorhandenen QSO geupdated. Erfolg, kein Reject."""
    def handler(req):
        return httpx.Response(200, text="Updated QSO")
    _patch_httpx(monkeypatch, handler)
    await upload_qso("me@example.com", "pw", "key", "DO3XR", _make_qso())


async def test_upload_ok_bare_duplicate_body(monkeypatch):
    """Bare 'Duplicate' (ohne QSO-Prefix) ist Erfolg, idempotent."""
    def handler(req):
        return httpx.Response(200, text="Duplicate")
    _patch_httpx(monkeypatch, handler)
    await upload_qso("me@example.com", "pw", "key", "DO3XR", _make_qso())


async def test_upload_rejects_unknown_200_body(monkeypatch):
    """200 mit irgendeinem anderen Text (z.B. Rate-Limit, HTML-Wartung)
    wird NICHT mehr als Erfolg gewertet."""
    def handler(req):
        return httpx.Response(200, text="Rate limit exceeded, try later")
    _patch_httpx(monkeypatch, handler)
    with pytest.raises(ClubLogError, match="(?i)rate limit"):
        await upload_qso("me@example.com", "pw", "key", "DO3XR", _make_qso())


async def test_upload_http_500(monkeypatch):
    """5xx → ClubLogError mit HTTP-Status (soft, Drain-Loop retried)."""
    def handler(req):
        return httpx.Response(500, text="server error")
    _patch_httpx(monkeypatch, handler)
    with pytest.raises(ClubLogError, match="HTTP 500"):
        await upload_qso("me@example.com", "pw", "key", "DO3XR", _make_qso())


async def test_upload_http_403_missing_api_key(monkeypatch):
    """403 = api_key fehlt/falsch (Sebastian-Live-Bug 2026-05-28)."""
    def handler(req):
        return httpx.Response(403, text="forbidden")
    _patch_httpx(monkeypatch, handler)
    with pytest.raises(ClubLogError, match="HTTP 403"):
        await upload_qso("me@example.com", "pw", "bad-key", "DO3XR", _make_qso())


# ---------------------------------------------------------------------------
# OperatorConfig — clublog fields
# ---------------------------------------------------------------------------


def test_operator_clublog_defaults_none():
    op = OperatorConfig(callsign="DO3XR")
    assert op.clublog_email is None
    assert op.clublog_app_password is None


def test_operator_clublog_credentials_stored():
    op = OperatorConfig(
        callsign="DO3XR",
        clublog_email="me@example.com",
        clublog_app_password="abc-123",
        clublog_api_key="deadbeef" * 5,
    )
    assert op.clublog_email == "me@example.com"
    assert op.clublog_app_password == "abc-123"
    assert op.clublog_api_key == "deadbeef" * 5


def test_operator_clublog_api_key_default_none():
    """v0.21.1 — api_key ist eigenes Feld, default None."""
    op = OperatorConfig(callsign="DO3XR")
    assert op.clublog_api_key is None


# ---------------------------------------------------------------------------
# v0.21.4 — bulk_upload via putlogs.php
# ---------------------------------------------------------------------------


def test_qsos_to_adif_multi_record():
    """Mehrere QSOs werden zu einem ADIF-Stream mit Header zusammengefuegt."""
    q1 = _make_qso(call="EA5KB")
    q2 = _make_qso(call="JA1XYZ")
    adif = _qsos_to_adif([q1, q2], "DO3XR")
    assert "<eoh>" in adif
    assert "<call:5>EA5KB" in adif
    assert "<call:6>JA1XYZ" in adif
    # Genau zwei EOR-Marker
    assert adif.count("<eor>") == 2


def test_qsos_to_adif_empty_list():
    adif = _qsos_to_adif([], "DO3XR")
    assert "<eoh>" in adif  # Header da, keine Records


async def test_bulk_upload_ok(monkeypatch):
    """putlogs.php POST mit multipart-Body. 200 OK → success."""
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["method"] = req.method
        seen["content_type"] = req.headers.get("content-type", "")
        seen["body"] = req.content
        return httpx.Response(200, text="OK — 3 records processed")

    _patch_httpx(monkeypatch, handler)
    qsos = [_make_qso(call=c) for c in ("EA5KB", "JA1XYZ", "K1JT")]
    await bulk_upload("me@example.com", "pw", "deadbeef-key", "DO3XR", qsos)

    assert seen["url"] == CLUBLOG_BULK_URL
    assert seen["method"] == "POST"
    # multipart/form-data Body enthaelt die Credentials + die ADIF-Datei
    assert b"multipart/form-data" in seen["content_type"].encode()
    assert b"me@example.com" in seen["body"]
    assert b"pw" in seen["body"]
    assert b"DO3XR" in seen["body"]
    assert b"deadbeef-key" in seen["body"]
    assert b"EA5KB" in seen["body"]
    assert b"JA1XYZ" in seen["body"]
    assert b"K1JT" in seen["body"]


async def test_bulk_upload_empty_list_is_noop(monkeypatch):
    """Leere Liste → keine Request, kein Crash."""
    seen = {"called": False}
    def handler(req):
        seen["called"] = True
        return httpx.Response(200)
    _patch_httpx(monkeypatch, handler)
    await bulk_upload("me@example.com", "pw", "key", "DO3XR", [])
    assert seen["called"] is False


async def test_bulk_upload_http_403_raises(monkeypatch):
    """403 = bad creds / api_key → ClubLogError mit HTTP-Hinweis."""
    def handler(req):
        return httpx.Response(403, text="Authentication failed")
    _patch_httpx(monkeypatch, handler)
    qsos = [_make_qso()]
    with pytest.raises(ClubLogError, match="HTTP 403"):
        await bulk_upload("me@example.com", "pw", "bad", "DO3XR", qsos)


async def test_bulk_upload_http_500_raises(monkeypatch):
    """5xx → ClubLogError, Drain-Loop soll spaeter retrien."""
    def handler(req):
        return httpx.Response(500, text="server error")
    _patch_httpx(monkeypatch, handler)
    qsos = [_make_qso()]
    with pytest.raises(ClubLogError, match="HTTP 500"):
        await bulk_upload("me@example.com", "pw", "key", "DO3XR", qsos)
