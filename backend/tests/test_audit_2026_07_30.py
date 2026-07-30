"""Regressionstests zum Code-Audit vom 2026-07-30.

Jeder Test hier haelt genau einen gefundenen Bug fest. Alle vier lagen in
ungetestetem Code — das ist der Grund, warum sie so lange ueberlebt haben.
"""

from __future__ import annotations

import httpx
import pytest

from ft8_appliance.integrations.clublog import ClubLogError, upload_qso
from ft8_appliance.statemachine.guards import (
    DEFAULT_GUARDS,
    GuardLimits,
    HardwareState,
    evaluate,
    first_failure,
    license_guard,
)
from ft8_appliance.util.redact import redact_secrets
from tests.test_clublog import _make_qso, _patch_httpx

# ---------------------------------------------------------------------------
# 1) Secret-Leak: ClubLog-API-Key landete ueber die httpx-Exception im
#    Preflight-detail und von dort per ntfy in ein oeffentliches Topic.


def test_redact_strips_clublog_api_key_from_httpx_message() -> None:
    """Exakt die Zeichenkette, die httpx bei raise_for_status() erzeugt."""
    msg = (
        "Client error '403 Forbidden' for url "
        "'https://clublog.org/watch.php?call=DO3XR&api=GEHEIM123'"
    )
    out = redact_secrets(msg)
    assert "GEHEIM123" not in out
    assert "api=***" in out
    # Call bleibt lesbar — sonst ist die Meldung fuer die Fehlersuche wertlos.
    assert "call=DO3XR" in out


def test_redact_strips_qrz_password() -> None:
    msg = "url 'https://xmldata.qrz.com/xml/?username=DO3XR&password=hunter2&agent=x'"
    out = redact_secrets(msg)
    assert "hunter2" not in out
    assert "password=***" in out
    assert "username=DO3XR" in out


@pytest.mark.parametrize("param", ["api", "api_key", "apikey", "key", "token", "secret"])
def test_redact_covers_common_secret_params(param: str) -> None:
    assert "SUPERGEHEIM" not in redact_secrets(f"?x=1&{param}=SUPERGEHEIM&y=2")


def test_redact_leaves_harmless_text_alone() -> None:
    """Kein blindes Zerschiessen von Freitext ohne Secrets."""
    msg = "ClubLog nicht erreichbar: ConnectTimeout"
    assert redact_secrets(msg) == msg


async def test_clublog_watch_403_does_not_leak_key(monkeypatch) -> None:
    """check_callsign_registered darf den Key nicht in die Exception schreiben.

    ClubLog antwortet bei falschem/abgelaufenem Key mit 403 — also genau
    dann, wenn der Preflight die Meldung anzeigt und pusht.
    """
    from ft8_appliance.integrations import clublog

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    _patch_httpx(monkeypatch, handler)
    with pytest.raises(ClubLogError) as exc:
        await clublog.check_callsign_registered("DO3XR", "GEHEIMER-KEY-42")
    assert "GEHEIMER-KEY-42" not in str(exc.value)
    assert "403" in str(exc.value)


# ---------------------------------------------------------------------------
# 2) Stiller QSO-Verlust: "OK" wurde als Substring gesucht und traf damit
#    auch auf HTML-Seiten, die ClubLog mit HTTP 200 ausliefert.


@pytest.mark.parametrize(
    "body",
    [
        "<html><title>Clublog Logbook</title>maintenance</html>",  # enthaelt "OK"
        "TOKEN EXPIRED",
        "BROKEN",
        "Booking failed",
    ],
)
async def test_clublog_200_html_is_not_success(monkeypatch, body: str) -> None:
    """Frueher Erfolg per Substring-Treffer → QSO wurde als hochgeladen
    markiert und war lautlos verloren."""
    assert "OK" in body.upper(), "Testfall trifft den Substring-Bug nicht"

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    _patch_httpx(monkeypatch, handler)
    with pytest.raises(ClubLogError):
        await upload_qso("me@example.com", "pw", "key", "DO3XR", _make_qso())


@pytest.mark.parametrize("body", ["OK", "QSO OK", "DUPLICATE", "UPDATED QSO"])
async def test_clublog_real_success_markers_still_pass(monkeypatch, body: str) -> None:
    """Gegenprobe: die echten Erfolgsantworten duerfen nicht kaputtgehen."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    _patch_httpx(monkeypatch, handler)
    await upload_qso("me@example.com", "pw", "key", "DO3XR", _make_qso())


# ---------------------------------------------------------------------------
# 3) Lizenz-Guard: can_tx_on() war implementiert und getestet, hing aber in
#    keinem Laufzeitpfad. Nur der Autopilot war lizenzbewusst.


def test_license_guard_blocks_unlicensed_band() -> None:
    res = license_guard(HardwareState(band_allowed_for_license=False), GuardLimits())
    assert res.ok is False
    assert res.code == "guard.license"


def test_license_guard_is_in_default_pipeline() -> None:
    """Der eigentliche Bug war das Fehlen in der Pipeline, nicht die Logik."""
    assert license_guard in DEFAULT_GUARDS


def test_unlicensed_band_fails_full_guard_pipeline() -> None:
    hw = HardwareState(chrony_synced=True, band_allowed_for_license=False)
    fail = first_failure(evaluate(hw, GuardLimits()))
    assert fail is not None
    assert fail.name == "license_guard"


def test_license_lock_has_its_own_reason_not_antenna() -> None:
    """Lizenz und Antenne brauchen verschiedene Abhilfen — die Begruendung
    darf sie nicht verwechseln."""
    hw = HardwareState(
        chrony_synced=True, band_allowed_for_license=False, antenna_covers_band=True,
    )
    fail = first_failure(evaluate(hw, GuardLimits()))
    assert fail is not None
    assert fail.code == "guard.license"


def test_licensed_band_passes() -> None:
    hw = HardwareState(chrony_synced=True)
    assert first_failure(evaluate(hw, GuardLimits())) is None


def test_guard_license_message_exists_in_both_languages() -> None:
    """Direkt gegen die Tabellen, nicht ueber translate(): translate() faellt
    bei fehlendem EN-Key stillschweigend auf DE zurueck und wuerde eine
    fehlende Uebersetzung darum gar nicht auffallen lassen."""
    from ft8_appliance.i18n import _DE, _EN

    assert "guard.license" in _DE
    assert "guard.license" in _EN
