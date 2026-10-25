"""POST /v1/help-requests: the Help form emails the expert's free text to our own support address (ADR 0022)."""

from __future__ import annotations

import logging

import psycopg
import pytest
from fastapi import FastAPI

from app.core.email import HELP_REQUEST, Mail
from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

SUPPORT = "hello@fourintegers.com"
TEXT = "Mrs Zed Quillfeather, DOB 01/02/1960, cannot open her pages"
GOOD = {"topic": "case", "message": TEXT, "page": "/cases/abc/read"}


async def test_a_request_sends_one_mail_to_the_support_address(seed: Seed, client_for: ClientFor, app: FastAPI) -> None:
    person = seed.person()
    client = await client_for(person, email="expert@example.test")
    resp = await client.post("/v1/help-requests", json=GOOD)
    assert resp.status_code == 202
    sent = [m for m in app.state.mailer.outbox if m.template == HELP_REQUEST]
    assert len(sent) == 1
    assert sent[0].to == SUPPORT
    assert sent[0].reply_to == "expert@example.test"
    assert sent[0].params["message"] == TEXT
    assert sent[0].params["topic"] == "case"
    assert TEXT not in repr(sent[0])


@pytest.mark.parametrize(
    "body",
    [
        {**GOOD, "topic": "weather"},
        {**GOOD, "message": "   "},
        {**GOOD, "message": "x" * 4001},
        {**GOOD, "message": "bell\x07"},
        {**GOOD, "page": "https://evil.example/"},
        {**GOOD, "page": "/a b"},
        {"topic": "case", "message": "hi"},
    ],
)
async def test_bad_input_is_422_and_sends_nothing(seed: Seed, client_for: ClientFor, app: FastAPI, body: dict) -> None:
    client = await client_for(seed.person())
    assert (await client.post("/v1/help-requests", json=body)).status_code == 422
    assert not app.state.mailer.outbox


async def test_newlines_and_tabs_are_allowed(seed: Seed, client_for: ClientFor, app: FastAPI) -> None:
    client = await client_for(seed.person())
    resp = await client.post("/v1/help-requests", json={**GOOD, "message": "line one\n\tline two"})
    assert resp.status_code == 202


async def test_the_sixth_in_a_day_is_429(seed: Seed, client_for: ClientFor, app: FastAPI) -> None:
    client = await client_for(seed.person())
    for _ in range(5):
        assert (await client.post("/v1/help-requests", json=GOOD)).status_code == 202
    resp = await client.post("/v1/help-requests", json=GOOD)
    assert resp.status_code == 429
    assert len(app.state.mailer.outbox) == 5


async def test_it_needs_a_signed_in_person_and_csrf(seed: Seed, client_for: ClientFor) -> None:
    assert (await (await client_for(None)).post("/v1/help-requests", json=GOOD)).status_code == 401
    no_csrf = await client_for(seed.person(), csrf=False)
    assert (await no_csrf.post("/v1/help-requests", json=GOOD)).status_code == 403


async def test_the_cell_says_a_help_form_exists_but_not_the_address(client_for: ClientFor) -> None:
    body = (await (await client_for(None)).get("/v1/cell")).json()
    assert body["help_form"] is True
    assert SUPPORT not in str(body)


async def test_the_text_is_in_no_log_and_no_audit_row(
    seed: Seed, client_for: ClientFor, admin: psycopg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    person = seed.person()
    client = await client_for(person)
    assert (await client.post("/v1/help-requests", json=GOOD)).status_code == 202
    assert "Quillfeather" not in caplog.text and SUPPORT not in caplog.text
    rows = admin.execute("select action, target from app.audit_log where person_id = %s", (person,)).fetchall()
    assert [r[0] for r in rows].count("help.request_sent") == 1
    assert "Quillfeather" not in str(rows)


async def test_a_failed_send_says_so_and_counts_nothing(
    seed: Seed,
    client_for: ClientFor,
    app: FastAPI,
    admin: psycopg.Connection,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG)

    async def down(mail: Mail) -> None:
        raise RuntimeError(f"mail service down: {mail.params}")

    monkeypatch.setattr(app.state.mailer, "send", down)
    person = seed.person()
    client = await client_for(person)
    resp = await client.post("/v1/help-requests", json=GOOD)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "help_not_sent"
    assert "Quillfeather" not in caplog.text and "Quillfeather" not in resp.text
    rows = admin.execute(
        "select 1 from app.audit_log where person_id = %s and action = 'help.request_sent'", (person,)
    ).fetchall()
    assert rows == []


def test_a_mail_never_shows_its_text_in_repr() -> None:
    mail = Mail(to=SUPPORT, template=HELP_REQUEST, params={"message": TEXT}, reply_to="a@b.test")
    assert TEXT not in repr(mail) and "a@b.test" not in repr(mail)
