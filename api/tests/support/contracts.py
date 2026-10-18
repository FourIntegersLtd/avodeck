"""Arrange rows in the contract tables (`facts`, `questions`, `report_versions`) as the local admin role.

Values are made up and short; nothing here is a real record. Rows go through the state-machine
triggers like any other write, so a helper that reaches a status walks the documented path.
"""

from __future__ import annotations

import uuid

import psycopg

# The only path through each state machine used to reach a status in tests.
FACT_PATH = {
    "suggested": [],
    "verified": ["verified"],
    "edited": ["edited"],
    "rejected": ["rejected"],
}
QUESTION_PATH = {"suggested": [], "confirmed": ["confirmed"], "edited": ["edited"]}


def add_fact(
    admin: psycopg.Connection,
    case: uuid.UUID,
    page: uuid.UUID,
    *,
    status: str = "suggested",
    value: str = "9 ug/L",
    word_ids: list[str] | None = None,
) -> uuid.UUID:
    fid = uuid.uuid4()
    admin.execute(
        "insert into app.facts (id, case_id, page_id, kind, value, word_ids) values (%s, %s, %s, 'zz_result', %s, %s)",
        (fid, case, page, value, ["w1", "w2"] if word_ids is None else word_ids),
    )
    for step in FACT_PATH[status]:
        admin.execute("update app.facts set status = %s where id = %s", (step, fid))
    return fid


def add_question(
    admin: psycopg.Connection,
    case: uuid.UUID,
    *,
    number: int = 1,
    status: str = "suggested",
    instruction_version_id: uuid.UUID | None = None,
) -> uuid.UUID:
    qid = uuid.uuid4()
    admin.execute(
        "insert into app.questions (id, case_id, instruction_version_id, number, text_verbatim, source_word_ids, kind)"
        " values (%s, %s, %s, %s, 'Was the standard met?', %s, 'zz_question')",
        (qid, case, instruction_version_id, number, ["w1"]),
    )
    for step in QUESTION_PATH[status]:
        admin.execute("update app.questions set status = %s where id = %s", (step, qid))
    return qid


def add_report_version(
    admin: psycopg.Connection, case: uuid.UUID, *, version_no: int = 1, status: str = "draft"
) -> uuid.UUID:
    rid = uuid.uuid4()
    admin.execute(
        "insert into app.report_versions (id, case_id, version_no, type, display_name)"
        " values (%s, %s, %s, 'main', 'Report 1')",
        (rid, case, version_no),
    )
    if status in ("locked", "served", "withdrawn"):
        admin.execute("update app.report_versions set status = 'locked', sha256 = %s where id = %s", ("a" * 64, rid))
    if status in ("served", "withdrawn"):
        admin.execute("update app.report_versions set status = 'served' where id = %s", (rid,))
    if status == "withdrawn":
        admin.execute(
            "update app.report_versions set status = 'withdrawn', withdrawn_reason = 'zz_reason' where id = %s",
            (rid,),
        )
    return rid


def add_instruction_version(
    admin: psycopg.Connection, case: uuid.UUID, *, version_no: int = 1, kind: str = "letter"
) -> uuid.UUID:
    """An unlocked instruction version (slice C fills the contract table `instruction_versions`)."""
    vid = uuid.uuid4()
    admin.execute(
        "insert into app.instruction_versions (id, case_id, version_no, kind, received_on)"
        " values (%s, %s, %s, %s, '2026-01-05')",
        (vid, case, version_no, kind),
    )
    return vid


def add_sent_invoice(
    admin: psycopg.Connection, case: uuid.UUID, owner: uuid.UUID, *, credited: str | None = None
) -> uuid.UUID:
    """A sent invoice of 100.00 + 20.00 tax (XTS) from the person's billing profile (numbered `ZZ-<n>` when
    sent), a draft beside it that is never exported, and optionally a credit note against the sent one."""
    admin.execute(
        "insert into app.billing_profiles (owner_type, owner_id, legal_name, currency, invoice_prefix)"
        " values ('person', %s, 'Zz Issuer', 'XTS', 'ZZ-') on conflict (owner_type, owner_id) do nothing",
        (owner,),
    )
    row = admin.execute(
        "select id from app.billing_profiles where owner_type = 'person' and owner_id = %s", (owner,)
    ).fetchone()
    assert row is not None
    ids = []
    for _ in range(2):
        made = admin.execute(
            "insert into app.invoices (case_id, billing_profile_id, lines, total, tax, currency)"
            " values (%s, %s, '[]', 120.00, 20.00, 'XTS') returning id",
            (case, row[0]),
        ).fetchone()
        assert made is not None
        ids.append(made[0])
    admin.execute("update app.invoices set status = 'sent', sent_to = 'zz-payer@example.test' where id = %s", (ids[0],))
    if credited is not None:
        admin.execute(
            "insert into app.credit_notes (case_id, invoice_id, amount, reason) values (%s, %s, %s, 'zz_reason')",
            (case, ids[0], credited),
        )
    return uuid.UUID(str(ids[0]))
