"""Slice B, screen S09: corrections and the facts around them (decision 0018; rules 2 and 8).

- A new reading that replaces an earlier one re-checks every checked fact citing any word of the earlier
  reading, not only the new run: "Please check" on each fact the page no longer shows.
- A correction may not change words that a fact in a served (or withdrawn) report cites, nor replace an
  earlier reading of them: 409 `served_use_supplementary`, nothing saved.
- "Not right" on a fact cited in a served report is 409 `served_use_supplementary`, like edit and move.
- `fact_verifications.correction_id` records the expert's reading a check relied on.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from tests.http import test_reader_edit_checked_routes as edit_checked
from tests.http.test_reader_edit_checked_routes import checked, cite_in_report, facts, verify

app = edit_checked.app  # fixtures shared with the edit-checked tests
aconn = edit_checked.aconn
world = edit_checked.world

pytestmark = pytest.mark.anyio


def current(admin: psycopg.Connection, w: dict[str, Any]) -> list[tuple[Any, ...]]:
    return admin.execute(
        "select id, word_ids from app.word_corrections where page_id = %s and superseded_at is null"
        " order by created_at, id",
        (w["page_id"],),
    ).fetchall()


def add_checked(
    admin: psycopg.Connection, w: dict[str, Any], like: dict[str, Any], value: str, word_ids: list[str]
) -> str:
    row = admin.execute(
        "insert into app.facts (case_id, page_id, kind, value, word_ids) values (%s, %s, %s, %s, %s) returning id",
        (w["case"], w["page_id"], like["kind"], value, word_ids),
    ).fetchone()
    assert row is not None
    admin.execute("update app.facts set status = 'verified' where id = %s", (row[0],))
    return str(row[0])


async def long_fact(w: dict[str, Any]) -> dict[str, Any]:
    return next(f for f in await facts(w) if f["value"] and len(f["word_ids"]) >= 3)


async def test_replacing_a_reading_rechecks_facts_on_any_word_it_covered(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    fact = await long_fact(world)
    a1, a2, a3 = fact["word_ids"][:3]
    first = await verify(
        world,
        fact,
        action="edit",
        new_value="tetanus booster",
        word_ids=fact["word_ids"],
        corrected_words={"word_ids": [a2, a3], "text": "tetanus booster"},
    )
    assert first.status_code == 200, first.text
    relying = add_checked(admin, world, fact, "tetanus booster", [a3])

    again = await verify(
        world,
        first.json(),
        action="edit",
        new_value="influenza",
        word_ids=fact["word_ids"],
        corrected_words={"word_ids": [a1, a2], "text": "influenza"},
    )
    assert again.status_code == 200, again.text
    shown = {f["id"]: f for f in await facts(world)}
    assert shown[relying]["needs_recheck"] is True, "the replaced reading no longer shows this fact"
    assert shown[relying]["cites_correction"] is False


async def test_a_correction_may_not_change_words_a_served_fact_cites(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    served = await checked(world)
    cite_in_report(admin, world, served, served=True)
    other = next(f for f in await facts(world) if f["value"] and f["id"] != served["id"])
    refused = await verify(
        world,
        other,
        action="edit",
        new_value="Tetanus booster given",
        word_ids=served["word_ids"],
        corrected_words={"word_ids": served["word_ids"], "text": "Tetanus booster given"},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "served_use_supplementary"
    assert current(admin, world) == []


async def test_a_correction_may_not_replace_an_earlier_reading_of_a_served_facts_words(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    fact = await long_fact(world)
    a1, a2, a3 = fact["word_ids"][:3]
    done = await verify(
        world,
        fact,
        action="edit",
        new_value="tetanus booster",
        word_ids=fact["word_ids"],
        corrected_words={"word_ids": [a2, a3], "text": "tetanus booster"},
    )
    assert done.status_code == 200, done.text
    served_id = add_checked(admin, world, fact, "tetanus booster", [a3])
    served = next(f for f in await facts(world) if f["id"] == served_id)
    cite_in_report(admin, world, served, served=True)

    refused = await verify(
        world,
        done.json(),
        action="edit",
        new_value="influenza",
        word_ids=fact["word_ids"],
        corrected_words={"word_ids": [a1, a2], "text": "influenza"},
    )
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "served_use_supplementary"
    assert [r[1] for r in current(admin, world)] == [[a2, a3]]


@pytest.mark.parametrize("withdrawn", [False, True])
async def test_not_right_on_a_fact_in_a_served_report_goes_through_the_supplementary_report(
    admin: psycopg.Connection, world: dict[str, Any], withdrawn: bool
) -> None:
    fact = await checked(world)
    cite_in_report(admin, world, fact, served=True)
    if withdrawn:
        admin.execute(
            "update app.report_versions set status = 'withdrawn', withdrawn_reason = 'mistake', withdrawn_at = now()"
            " where case_id = %s and status = 'served'",
            (world["case"],),
        )
    refused = await verify(world, fact, action="not_right")
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "served_use_supplementary"
    row = admin.execute("select status, version from app.facts where id = %s", (fact["id"],)).fetchone()
    assert row == ("verified", fact["version"])


async def test_the_check_log_records_the_reading_it_relied_on(admin: psycopg.Connection, world: dict[str, Any]) -> None:
    fact = await checked(world)
    changed = await verify(
        world,
        fact,
        action="edit",
        new_value="Tetanus booster given",
        word_ids=fact["word_ids"],
        corrected_words={"word_ids": fact["word_ids"], "text": "Tetanus booster given"},
    )
    assert changed.status_code == 200, changed.text
    (correction_id, _), *_ = current(admin, world)
    logged = admin.execute(
        "select action, correction_id from app.fact_verifications where fact_id = %s order by seq", (fact["id"],)
    ).fetchall()
    assert logged == [("correct", None), ("edit", correction_id)]

    again = await verify(world, changed.json(), action="edit", new_value="Tetanus booster")
    assert again.status_code == 200, again.text
    last = admin.execute(
        "select correction_id from app.fact_verifications where fact_id = %s order by seq desc limit 1", (fact["id"],)
    ).fetchone()
    assert last == (correction_id,), "a change still citing the expert's reading relies on it"
    ok = admin.execute("select app.fact_verifications_chain_ok(%s)", (world["case"],)).fetchone()
    assert ok == (True,)
