"""Slice D audit fixes over HTTP (docs/slices/D-understand.md, architecture 10.4 and 10.5).

- A note on a consultation is the author's own: nobody else on the case sees it, and when the named
  expert changes the new expert writes their own note while the author keeps and may still change theirs.
- The timeline's "checked" filter (`status=verified`) shows edited facts too (checked = verified + edited).
- A decided finding that a re-run no longer finds stays listed, marked "no longer found" (`status: gone`).
- A link whose question was removed is kept as `stale` (never deleted) and cannot be confirmed.
- A link can be added by hand only to a confirmed question (read through `confirmed_questions`).
- Links carry the entry's kind, page and date so the expert can review them.
- `GET /consultations/{id}?on=` shows the record as it stood on a chosen day.
- The guideline version each case was shown is recorded; a later change to that entry shows a notice
  (`guidance_changed`) on the consultation and on the timeline.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest

from app.features.understand.guidelines import in_force
from tests.http.test_understand_routes_timeline import Case, _plain_lanes, _visit_kind
from tests.support.db import ClientFor
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def _code(resp: Any) -> str:
    return str(resp.json()["error"]["code"])


# --- a consultation note is private to its author -------------------------------------------------------


async def test_a_note_is_seen_only_by_its_author(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    visit = case.fact(_visit_kind(case.pack), date(2024, 5, 15), available=date(2024, 5, 15))
    url = f"/v1/cases/{case.id}/consultations/{visit}"
    expert = await client_for(case.expert)
    assert (await expert.put(f"{url}/note", json={"body": "only mine"})).status_code == 200
    assert (await expert.get(url)).json()["note"] == "only mine"
    assert (await (await client_for(case.assistant)).get(url)).json()["note"] is None


async def test_after_the_named_expert_changes_each_keeps_their_own_note(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    visit = case.fact(_visit_kind(case.pack), date(2024, 5, 15), available=date(2024, 5, 15))
    url = f"/v1/cases/{case.id}/consultations/{visit}"
    first = await client_for(case.expert)
    assert (await first.put(f"{url}/note", json={"body": "first expert"})).status_code == 200
    # The assistant becomes the named expert; the first expert stays on the case as a co-expert.
    case.admin.execute("update app.case_members set removed_at = now() where case_id = %s and person_id = %s",
                       (case.id, case.assistant))  # fmt: skip
    case.admin.execute("update app.cases set named_expert_id = %s where id = %s", (case.assistant, case.id))
    case.admin.execute(
        "insert into app.case_members (case_id, person_id, case_role, added_by) values (%s, %s, 'co_expert', %s)",
        (case.id, case.expert, case.assistant),
    )
    second = await client_for(case.assistant)
    # Scenario S3: the change put every checked fact back to `suggested`; the new expert reads the page
    # and checks the visit again, through the normal API, before the consultation opens for them.
    assert (await second.post(f"/v1/cases/{case.id}/pages/1/read", json={"dwell_seconds": 30})).status_code == 200
    facts = (await second.get(f"/v1/cases/{case.id}/pages/1/facts")).json()["facts"]
    [shown] = [f for f in facts if f["id"] == str(visit)]
    assert shown["status"] == "suggested"
    checked = await second.post(
        f"/v1/cases/{case.id}/facts/{visit}/verify",
        json={"action": "correct"},
        headers={"If-Match": f'"{shown["version"]}"'},
    )
    assert checked.status_code == 200, checked.text
    assert (await second.get(url)).json()["note"] is None, "the new expert never sees the old note"
    assert (await second.put(f"{url}/note", json={"body": "second expert"})).status_code == 200
    assert (await second.get(url)).json()["note"] == "second expert"
    assert (await first.put(f"{url}/note", json={"body": "first, changed"})).status_code == 200
    assert (await first.get(url)).json()["note"] == "first, changed"
    assert (await second.get(url)).json()["note"] == "second expert"


# --- the timeline's checked filter ------------------------------------------------------------------


async def test_checked_means_verified_or_edited(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    verified = case.fact(kind, date(2024, 1, 10))
    edited = case.fact(kind, date(2024, 5, 1), status="edited")
    case.fact(kind, date(2024, 5, 2), status="suggested")
    resp = await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/timeline", params={"status": "verified"})
    assert resp.status_code == 200, resp.text
    got = {e["fact_id"] for lane in resp.json()["lanes"] for e in lane["events"]}
    assert got == {str(verified), str(edited)}


# --- findings no longer found ------------------------------------------------------------------------


def _finding(case: Case, fact: uuid.UUID, *, status: str = "open", decision: str | None = None) -> uuid.UUID:
    check = case.pack.checks()[0]
    row = case.admin.execute(
        "insert into app.findings (case_id, check_key, rule, evidence_fact_ids, evidence_page_ids, pack_version)"
        " values (%s, %s, %s, %s, %s, 'zz@1') returning id",
        (case.id, check["key"], check["rule"], [fact], [case.page(1)]),
    ).fetchone()
    assert row is not None
    if decision is not None:
        case.admin.execute("select set_config('app.person_id', %s, false)", (str(case.expert),))
        case.admin.execute("update app.findings set decision = %s where id = %s", (decision, row[0]))
        case.admin.execute("select set_config('app.person_id', '', false)")
    if status != "open":
        case.admin.execute("update app.findings set status = %s where id = %s", (status, row[0]))
    return uuid.UUID(str(row[0]))


async def test_a_decided_finding_no_longer_found_stays_listed_and_says_so(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    still = _finding(case, case.fact(kind, date(2024, 1, 10)))
    decided_gone = _finding(case, case.fact(kind, date(2024, 1, 11)), status="gone", decision="relevant")
    _finding(case, case.fact(kind, date(2024, 1, 12)), status="gone")
    resp = await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/findings")
    assert resp.status_code == 200, resp.text
    got = {f["id"]: f for f in resp.json()["findings"]}
    assert set(got) == {str(still), str(decided_gone)}
    assert (got[str(still)]["status"], got[str(decided_gone)]["status"]) == ("open", "gone")
    assert got[str(decided_gone)]["decision"] == "relevant"


# --- links -----------------------------------------------------------------------------------------------


async def test_a_link_whose_question_was_removed_is_kept_as_stale_and_cannot_be_confirmed(
    client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed)
    fact, question = case.fact(_plain_lanes(case.pack)[0]["kinds"][0], date(2024, 1, 10)), case.question()
    link = case.link(fact, question)
    case.admin.execute("delete from app.questions where id = %s", (question,))
    row = case.admin.execute("select status, question_id from app.fact_question_links where id = %s", (link,))
    assert row.fetchone() == ("stale", None)
    expert = await client_for(case.expert)
    [listed] = (await expert.get(f"/v1/cases/{case.id}/fact-links")).json()["links"]
    assert (listed["id"], listed["status"], listed["question_id"]) == (str(link), "stale", None)
    resp = await expert.patch(
        f"/v1/cases/{case.id}/fact-links/{link}", json={"status": "confirmed"}, headers={"If-Match": '"2"'}
    )
    assert resp.status_code == 409 and _code(resp) == "stale_link"


async def test_a_link_by_hand_needs_a_confirmed_question(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    fact = case.fact(_plain_lanes(case.pack)[0]["kinds"][0], date(2024, 1, 10))
    row = case.admin.execute(
        "insert into app.questions (case_id, number, text_verbatim, kind) values (%s, 2, 'zz?', 'zz') returning id",
        (case.id,),
    ).fetchone()
    assert row is not None
    resp = await (await client_for(case.expert)).post(
        f"/v1/cases/{case.id}/fact-links", json={"fact_id": str(fact), "question_id": str(row[0])}
    )
    assert resp.status_code == 404


async def test_links_say_which_entry_they_tie_to(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    fact, question = case.fact(kind, date(2024, 1, 10), page=402), case.question()
    case.link(fact, question)
    [listed] = (await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/fact-links")).json()["links"]
    assert (listed["kind"], listed["page_no"], listed["event_date"]) == (kind, 402, "2024-01-10")


# --- one consultation on a chosen day ----------------------------------------------------------------------


async def test_the_record_can_be_seen_as_it_stood_on_a_chosen_day(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    arriving = sorted(case.pack.arriving_kinds())[0]
    visit = case.fact(_visit_kind(case.pack), date(2024, 5, 15), page=118, available=date(2024, 5, 15))
    later = case.fact(arriving, date(2024, 5, 10), page=119, available=date(2024, 5, 22))
    client = await client_for(case.expert)
    url = f"/v1/cases/{case.id}/consultations/{visit}"
    on_the_day = (await client.get(url)).json()
    assert [e["fact_id"] for e in on_the_day["later"]] == [str(later)]
    after = await client.get(url, params={"on": "2024-05-23"})
    assert after.status_code == 200, after.text
    assert [e["fact_id"] for e in after.json()["visible"]] == [str(later)]
    assert after.json()["later_count"] == 0


# --- the guideline version each case was shown --------------------------------------------------------------


async def test_the_version_shown_is_recorded_and_a_later_change_shows_a_notice(
    client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed, LAUNCH)
    on = date(2024, 5, 15)
    shown = [r for r in case.pack.guidelines() if in_force([r], r["code"], on)]
    assert shown, "the launch overlay lists guidance in force on the planted day"
    visit = case.fact(_visit_kind(case.pack), on, available=on)
    client = await client_for(case.assistant)
    url = f"/v1/cases/{case.id}/consultations/{visit}"
    first = (await client.get(url)).json()
    assert first["guidance_changed"] == []
    rows = case.admin.execute(
        "select code, version_label, entry_hash from app.case_guideline_versions where case_id = %s order by code",
        (case.id,),
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == sorted((str(r["code"]), str(r["version_label"])) for r in shown)
    assert all(r[2] for r in rows)
    code = rows[0][0]
    case.admin.execute(
        "update app.case_guideline_versions set entry_hash = 'older' where case_id = %s and code = %s", (case.id, code)
    )
    assert (await client.get(url)).json()["guidance_changed"] == [code]
    timeline = (await client.get(f"/v1/cases/{case.id}/timeline")).json()
    assert timeline["guidance_changed"] == [code]
