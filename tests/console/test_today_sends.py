from types import SimpleNamespace

from spark_console.today_sends import account_health_view
from spark_console.web.app import run_view_status


def test_run_status_uses_three_tones():
    success = run_view_status(SimpleNamespace(status="success", error_code=None, submitted_at=None))
    missed = run_view_status(SimpleNamespace(status="failed", error_code="health_retry_exhausted", submitted_at=None))
    login = run_view_status(SimpleNamespace(status="failed", error_code="cookie_invalid", submitted_at=None))
    assert success == {"label": "已发出", "tone": "good", "kind": "success"}
    assert missed["tone"] == "warn" and missed["kind"] == "missed"
    assert login["tone"] == "danger" and login["kind"] == "login"


def test_unconfirmed_and_retry_stay_yellow():
    waiting = run_view_status(SimpleNamespace(status="retrying", error_code="health_retry_waiting", submitted_at=None))
    pending = run_view_status(SimpleNamespace(status="submitted_unconfirmed", error_code=None, submitted_at=None))
    assert waiting["tone"] == "warn"
    assert pending == {"label": "待核实", "tone": "warn", "kind": "unconfirmed"}


def test_account_health_tones():
    assert account_health_view("valid")["tone"] == "good"
    assert account_health_view("temporary")["tone"] == "warn"
    assert account_health_view("verification")["tone"] == "danger"
    assert account_health_view("invalid")["tone"] == "danger"
