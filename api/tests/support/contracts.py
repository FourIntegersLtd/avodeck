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
    admin: psycopg.Connection, case: uuid.UUID, *, number: int = 1, status: str = "suggested"
) -> uuid.UUID:
    qid = uuid.uuid4()
    admin.execute(
        "insert into app.questions (id, case_id, number, text_verbatim, source_word_ids, kind)"
        " values (%s, %s, %s, 'Was the standard met?', %s, 'zz_question')",
        (qid, case, number, ["w1"]),
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
