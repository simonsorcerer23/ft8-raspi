"""DATA-H1/H2: Upload-Reject-Klassifizierung (hart vs. transient)."""
from ft8_appliance.runtime.orchestrator import Orchestrator as O


def test_hard_rejects():
    assert O._upload_reject_is_hard("Duplicate QSO")
    assert O._upload_reject_is_hard("403 Forbidden")
    assert O._upload_reject_is_hard("authentication failed")
    assert O._upload_reject_is_hard("invalid ADIF record")


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
