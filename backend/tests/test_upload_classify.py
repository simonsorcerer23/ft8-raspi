"""DATA-H1/H2: Upload-Reject-Klassifizierung (hart vs. transient)."""
from ft8_appliance.runtime.orchestrator import Orchestrator as O


def test_hard_rejects():
    assert O._upload_reject_is_hard("invalid ADIF record")
    assert O._upload_reject_is_hard("Invalid call")


def test_konto_fehler_sind_keine_harte_ablehnung_des_qso():
    """Audit 14.11.2026: "403" und "invalid api key" galten als harte Ablehnung;
    ein veralteter Schluessel liess alle offenen QSOs als hochgeladen verbuchen."""
    for text in ("403 Forbidden", "authentication failed", "ClubLog bulk HTTP 403: Forbidden",
                 "invalid api key provided"):
        assert O._upload_reject_ist_konto(text), text
        assert not O._upload_reject_is_hard(text), text
    assert not O._upload_reject_ist_konto("could not reach login server")   # transient
    assert not O._upload_reject_ist_konto("invalid ADIF record")


def test_transient_not_hard():
    assert not O._upload_reject_is_hard("rate limit exceeded")
    assert not O._upload_reject_is_hard("503 Service Unavailable")
    assert not O._upload_reject_is_hard("connection reset")
    assert not O._upload_reject_is_hard("")


def test_transient_wins_over_hard_substring():
    # H2-Footgun: enthaelt 'login' (hart-Liste) ABER 'could not reach'
    # (transient) → muss transient sein, sonst ganze Bulk-Charge verloren.
    assert not O._upload_reject_is_hard("could not reach login server")
    assert not O._upload_reject_is_hard("timeout during authentication")


def test_dupe_ist_kein_harter_reject():
    """Ein Dupe heisst: das QSO liegt drueben. Das ist ein Erfolg und
    gehoert nicht in dieselbe Schublade wie ein Auth-Fehler — der
    Bulk-Pfad wendet einen harten Reject auf die GANZE Charge an."""
    for msg in ("ClubLog rejected: Dupe", "Duplicate QSO", "already logged"):
        assert O._upload_reject_ist_dupe(msg), msg
        assert not O._upload_reject_is_hard(msg), msg


def test_dupe_erkennt_clublogs_echten_wortlaut():
    """Regression 2026-09-12: die Marker sagten "duplicate", ClubLog sagt
    "Dupe". Kein Marker passte, zwei QSOs liefen 12 Wiederholungen lang."""
    assert O._upload_reject_ist_dupe("ClubLog rejected: Dupe")


def test_harter_reject_ist_kein_dupe():
    for msg in ("403 Forbidden", "invalid ADIF record", "bad password"):
        assert not O._upload_reject_ist_dupe(msg), msg


def test_transientes_gewinnt_auch_gegen_dupe():
    """Wenn die Fehlermeldung transient UND dupe-verdaechtig klingt,
    darf sie das QSO nicht als erledigt abhaken."""
    assert not O._upload_reject_ist_dupe("timeout while checking duplicate")
