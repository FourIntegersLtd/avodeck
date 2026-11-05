"""Rule 6: a sampled performance trace is scrubbed like an error report (the SDK skips before_send for these)."""

from __future__ import annotations

from typing import Any

from app.core import logs

DSN = "https://public@sentry.invalid/1"
NUMBER = "9434765919"
NAME = "Pemberton"


def transaction() -> dict[str, Any]:
    return {
        "type": "transaction",
        "transaction": "/v1/cases/{case_id}",
        "request": {"method": "GET", "url": f"https://x/v1/cases?q={NUMBER}", "query_string": f"q={NUMBER}"},
        "user": {"id": NAME},
        "contexts": {"trace": {"op": "http.server", "data": {"http.query": f"q={NUMBER}", "url.path": NAME}}},
        "tags": {"note": f"ref {NUMBER}"},
        "spans": [
            {"op": "db", "description": f"select * from t where n = '{NUMBER}'", "data": {"x": NAME}},
            {"op": "http.client", "description": f"GET https://x/?q={NUMBER}", "tags": {"q": NUMBER}},
        ],
    }


def test_transactions_are_scrubbed_before_they_leave_the_process() -> None:
    options = logs.sentry_options(DSN, environment="test")
    out = options["before_send_transaction"](transaction(), {})
    assert out is not None
    text = repr(out)
    assert NUMBER not in text
    assert NAME not in text
    assert out["request"] == {"method": "GET"}
    assert "user" not in out
