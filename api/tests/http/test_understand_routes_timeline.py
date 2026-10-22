"""Screens 11 and 12 over HTTP (architecture 5.2 "11 Timeline" and "12 One consultation", slice D brief).

- `GET /timeline`: the lanes, their order and labels come from the case's pack; each event carries its
  status and the questions it is linked to; filters by date range, status and linked question.
  Suggestions on pages the person has not read stay hidden (rule 3); rejected facts never show.
- `/fact-links` (the brief's `/links`; that path already serves linked cases): any reader asks for
  suggestions (202, `suggest_links` job); only the named expert confirms, rejects or adds by hand,
  only on checked facts (409 otherwise); every decision is audited.
- `GET /consultations/{fact_id}?as_seen=true`: what had arrived by that date, what came later
  (counted, kept apart), and what nobody can tell ("not known"), never placed before the visit;
  the title and the guidance in force come from the pack.
- `PATCH /facts/{fact_id}/availability`: the named expert, always with a reason; appended and
  audited; the latest change wins.
- `PUT /consultations/{fact_id}/note`: the named expert's own note.
- `GET /v1/guidelines/in-force`: the version label and link in force on a date, else 404 `not_found`
  (the fixture packs ship no overlay, so under them nothing is in force).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from psycopg.types.json import Jsonb

from app.core.packs import default_library
from app.features.understand.guidelines import in_force
from tests.support.db import ClientFor
from tests.support.packs import BOTH, FIXTURE, LAUNCH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


class Case:
    def __init__(self, seed: Seed, combo: Combo = FIXTURE) -> None:
        self.seed, self.admin = seed, seed.admin
        self.expert, self.assistant, self.stranger = seed.person(), seed.person(), seed.person()
        org = seed.org(kind="team", members={self.expert: "expert", self.assistant: "assistant"})
        seed.org(kind="personal", members={self.stranger: "owner"})
        role = combo.raw("case-type", "pack.yaml")["expert_roles"][0]
        packs = {"jurisdiction": combo.jurisdiction, "case_type": combo.case_type, "specialty": combo.specialty,
                 "expert_role": role}  # fmt: skip
        self.id = seed.case(org, self.expert, status="active", members={self.assistant: "assistant"}, packs=packs)
        self.pack = default_library().combine(*combo.codes)
        self.doc = seed.document(self.id)
        self.pages: dict[int, uuid.UUID] = {}

    def page(self, n: int) -> uuid.UUID:
        if n not in self.pages:
            self.pages[n] = self.seed.page(self.id, self.doc, n)
        return self.pages[n]

    def fact(
        self,
        kind: str,
        on: date | None,
        *,
        page: int = 1,
        status: str = "verified",
        available: date | None = None,
        **fields: Any,
    ) -> uuid.UUID:
        at = datetime(available.year, available.month, available.day, tzinfo=UTC) if available else None
        row = self.admin.execute(
            "insert into app.facts (case_id, page_id, kind, fields, value, word_ids, event_date, available_at,"
            " availability_basis) values (%s, %s, %s, %s, 'zz', '{w1}', %s, %s, %s) returning id",
            (self.id, self.page(page), kind, Jsonb(fields), on, at, "zz_mark" if at else None),
        ).fetchone()
        assert row is not None
        if status != "suggested":
            self.admin.execute("update app.facts set status = %s where id = %s", (status, row[0]))
        return uuid.UUID(str(row[0]))

    def question(self, number: int = 1) -> uuid.UUID:
        row = self.admin.execute(
            "insert into app.questions (case_id, number, text_verbatim, kind) values (%s, %s, 'zz?', 'zz')"
            " returning id",
            (self.id, number),
        ).fetchone()
        assert row is not None
        self.admin.execute("update app.questions set status = 'confirmed' where id = %s", (row[0],))
        return uuid.UUID(str(row[0]))

    def link(self, fact: uuid.UUID, question: uuid.UUID) -> uuid.UUID:
        row = self.admin.execute(
            "insert into app.fact_question_links (case_id, fact_id, question_id, source, status)"
            " values (%s, %s, %s, 'expert', 'confirmed') returning id",
            (self.id, fact, question),
        ).fetchone()
        assert row is not None
        return uuid.UUID(str(row[0]))

    def audit(self) -> list[str]:
        rows = self.admin.execute("select action from app.audit_log where case_id = %s order by seq", (self.id,))
        return [r[0] for r in rows]

    def jobs(self, kind: str) -> int:
        row = self.admin.execute("select count(*) from app.jobs where case_id = %s and kind = %s", (self.id, kind))
        return int(row.fetchone()[0])  # type: ignore[index]


def _plain_lanes(pack: Any) -> list[dict[str, Any]]:
    return [lane for lane in pack.lanes() if lane.get("kinds") and not lane.get("when")]


def _code(resp: Any) -> str:
    return str(resp.json()["error"]["code"])


# --- the timeline ---------------------------------------------------------------------------------


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_the_lanes_their_order_and_labels_come_from_the_case_s_pack(
    combo: Combo, client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed, combo)
    placed = {lane["key"]: case.fact(lane["kinds"][0], date(2024, 5, 1)) for lane in _plain_lanes(case.pack)}
    resp = await (await client_for(case.assistant)).get(f"/v1/cases/{case.id}/timeline")
    assert resp.status_code == 200
    lanes = resp.json()["lanes"]
    assert [lane["key"] for lane in lanes] == [lane["key"] for lane in case.pack.lanes()]
    assert {lane["key"]: lane["label"] for lane in lanes} == {
        lane["key"]: case.pack.vocabulary["lanes"][lane["key"]] for lane in case.pack.lanes()
    }
    got = {lane["key"]: [e["fact_id"] for e in lane["events"]] for lane in lanes}
    for key, fact in placed.items():
        assert got[key] == [str(fact)]


async def test_each_event_shows_its_status_and_questions_and_the_filters_narrow_it(
    client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    early = case.fact(kind, date(2024, 1, 10))
    edited = case.fact(kind, date(2024, 5, 1), status="edited")
    case.fact(kind, date(2024, 5, 2), status="rejected")
    question = case.question()
    case.link(edited, question)
    client = await client_for(case.expert)

    def events(resp: Any) -> dict[str, dict[str, Any]]:
        assert resp.status_code == 200, resp.text
        return {e["fact_id"]: e for lane in resp.json()["lanes"] for e in lane["events"]}

    everything = events(await client.get(f"/v1/cases/{case.id}/timeline"))
    assert set(everything) == {str(early), str(edited)}, "rejected facts never show"
    assert everything[str(edited)]["status"] == "edited"
    assert everything[str(edited)]["question_ids"] == [str(question)]
    assert set(events(await client.get(f"/v1/cases/{case.id}/timeline", params={"status": "edited"}))) == {str(edited)}
    window = {"from": "2024-04-01", "to": "2024-06-30"}
    assert set(events(await client.get(f"/v1/cases/{case.id}/timeline", params=window))) == {str(edited)}
    linked = {"question_id": str(question)}
    assert set(events(await client.get(f"/v1/cases/{case.id}/timeline", params=linked))) == {str(edited)}


async def test_suggestions_on_unread_pages_stay_hidden(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    case.fact(kind, date(2024, 1, 10), status="suggested")
    resp = await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/timeline")
    assert [e for lane in resp.json()["lanes"] for e in lane["events"]] == []


async def test_someone_outside_the_case_gets_404(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    assert (await (await client_for(case.stranger)).get(f"/v1/cases/{case.id}/timeline")).status_code == 404


async def test_counts_include_undated_facts(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    case.fact(kind, date(2024, 5, 1))
    undated = case.fact(kind, None)
    resp = await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/timeline")
    assert resp.status_code == 200
    assert resp.json()["counts"]["undated"] == 1
    ids = {e["fact_id"] for lane in resp.json()["lanes"] for e in lane["events"]}
    assert str(undated) in ids or str(undated) in {e["fact_id"] for e in resp.json()["unplaced"]}


async def test_an_event_carries_how_its_date_was_got_and_where_its_first_word_sits(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    marked = case.fact(kind, date(1987, 5, 1))
    bare = case.fact(kind, date(2024, 5, 1), page=2)
    case.admin.execute("update app.facts set date_basis = 'printed_century' where id = %s", (marked,))
    case.admin.execute(
        "insert into app.ocr_words (page_id, case_id, n, word_id, text, polygon, confidence, source)"
        " values (%s, %s, 7, 'w1', 'x'::bytea, '{0,0,1,0,1,1,0,1}', 0.9, 'ocr')",
        (case.page(1), case.id),
    )
    resp = await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/timeline")
    got = {e["fact_id"]: e for lane in resp.json()["lanes"] for e in lane["events"]}
    got.update({e["fact_id"]: e for e in resp.json()["unplaced"]})
    assert got[str(marked)]["date_basis"] == "printed_century" and got[str(marked)]["first_word_n"] == 7
    assert got[str(bare)]["date_basis"] is None and got[str(bare)]["first_word_n"] is None


# --- links between facts and questions ------------------------------------------------------------


async def test_any_reader_asks_for_suggestions_and_a_job_is_queued(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    resp = await (await client_for(case.assistant)).post(f"/v1/cases/{case.id}/fact-links/suggest")
    assert resp.status_code == 202
    assert resp.json()["job_id"]
    assert case.jobs("suggest_links") == 1


async def test_only_the_named_expert_decides_a_suggested_link_and_it_is_audited(
    client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed)
    fact, question = case.fact(_plain_lanes(case.pack)[0]["kinds"][0], date(2024, 1, 10)), case.question()
    case.admin.execute(
        "insert into app.fact_question_links (case_id, fact_id, question_id, source, reason)"
        " values (%s, %s, %s, 'ai', 'zz')",
        (case.id, fact, question),
    )
    expert = await client_for(case.expert)
    listed = (await expert.get(f"/v1/cases/{case.id}/fact-links", params={"status": "suggested"})).json()
    [link] = listed["links"]
    assert (link["fact_id"], link["question_id"], link["reason"]) == (str(fact), str(question), "zz")
    url = f"/v1/cases/{case.id}/fact-links/{link['id']}"
    headers = {"If-Match": f'"{link["version"]}"'}
    refused = await (await client_for(case.assistant)).patch(url, json={"status": "confirmed"}, headers=headers)
    assert refused.status_code == 403
    resp = await expert.patch(url, json={"status": "confirmed"}, headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "confirmed"
    assert "link.confirmed" in case.audit()
    again = await expert.patch(url, json={"status": "rejected"}, headers={"If-Match": resp.headers["etag"]})
    assert again.status_code == 200
    assert "link.rejected" in case.audit()


async def test_the_named_expert_adds_a_link_by_hand_only_on_a_checked_fact(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    checked, unchecked = case.fact(kind, date(2024, 1, 10)), case.fact(kind, date(2024, 1, 11), status="suggested")
    question = case.question()
    expert = await client_for(case.expert)
    url = f"/v1/cases/{case.id}/fact-links"
    refused = await expert.post(url, json={"fact_id": str(unchecked), "question_id": str(question)})
    assert refused.status_code == 409 and _code(refused) == "not_checked"
    assistant = await client_for(case.assistant)
    assert (await assistant.post(url, json={"fact_id": str(checked), "question_id": str(question)})).status_code == 403
    resp = await expert.post(url, json={"fact_id": str(checked), "question_id": str(question)})
    assert resp.status_code == 201
    assert (resp.json()["status"], resp.json()["source"]) == ("confirmed", "expert")
    assert "link.added" in case.audit()


async def test_a_link_whose_fact_was_rejected_shows_as_stale(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    fact, question = case.fact(_plain_lanes(case.pack)[0]["kinds"][0], date(2024, 1, 10)), case.question()
    case.link(fact, question)
    case.admin.execute("update app.facts set status = 'rejected' where id = %s", (fact,))
    links = (await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/fact-links")).json()["links"]
    assert [link["status"] for link in links] == ["stale"]


# --- one consultation, as the clinician saw it ----------------------------------------------------


def _visit_kind(pack: Any) -> str:
    """A kind the pack does not mark as arriving: the clinician's own entry."""
    arriving = set(pack.arriving_kinds())
    return str(next(k["key"] for k in pack.fact_kinds() if k["key"] not in arriving))


async def test_as_seen_shows_what_had_arrived_keeps_later_apart_and_never_guesses(
    client_for: ClientFor, seed: Seed
) -> None:
    """Page 118 in miniature: filed the day before is visible, received a week later is not."""
    case = Case(seed)
    arriving = sorted(case.pack.arriving_kinds())[0]
    visit = case.fact(_visit_kind(case.pack), date(2024, 5, 15), page=118, available=date(2024, 5, 15))
    filed = case.fact(arriving, date(2024, 5, 14), page=117, available=date(2024, 5, 14))
    later = case.fact(arriving, date(2024, 5, 10), page=119, available=date(2024, 5, 22))
    unknown = case.fact(arriving, date(2024, 5, 1), page=120)
    case.fact(arriving, date(2024, 5, 1), page=121, status="rejected", available=date(2024, 5, 1))
    resp = await (await client_for(case.assistant)).get(
        f"/v1/cases/{case.id}/consultations/{visit}", params={"as_seen": "true"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entry"]["fact_id"] == str(visit)
    assert [e["fact_id"] for e in body["visible"]] == [str(filed)]
    assert body["later_count"] == 1 and [e["fact_id"] for e in body["later"]] == [str(later)]
    assert [e["fact_id"] for e in body["unknown"]] == [str(unknown)]
    assert body["title"] == case.pack.term("as_seen_title")
    expected = [row["code"] for row in case.pack.guidelines() if in_force([row], row["code"], date(2024, 5, 15))]
    assert [g["code"] for g in body["guidance"]] == expected


async def test_a_future_dated_fact_with_no_arrival_is_not_listed_as_unknown_on_an_earlier_visit(
    client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed)
    visit = case.fact(_visit_kind(case.pack), date(2024, 5, 15), page=118, available=date(2024, 5, 15))
    case.fact(_visit_kind(case.pack), date(2024, 6, 1), page=119)
    resp = await (await client_for(case.expert)).get(
        f"/v1/cases/{case.id}/consultations/{visit}", params={"as_seen": "true"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["unknown"] == []


async def test_undated_consultation_returns_undated_entry_with_page_number(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    visit = case.fact(_visit_kind(case.pack), None, page=8)
    resp = await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/consultations/{visit}")
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "undated_entry"
    assert err["page_no"] == 8


async def test_overriding_availability_needs_a_reason_appends_is_audited_and_the_latest_wins(
    client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed)
    arriving = sorted(case.pack.arriving_kinds())[0]
    visit = case.fact(_visit_kind(case.pack), date(2024, 5, 15), available=date(2024, 5, 15))
    letter = case.fact(arriving, date(2024, 5, 10), page=2)
    expert = await client_for(case.expert)
    url = f"/v1/cases/{case.id}/facts/{letter}/availability"
    assert (await expert.patch(url, json={"available_at": "2024-05-12"})).status_code == 422
    assistant = await client_for(case.assistant)
    assert (await assistant.patch(url, json={"available_at": "2024-05-12", "reason": "zz"})).status_code == 403
    first = await expert.patch(url, json={"available_at": "2024-05-12", "reason": "stamp on page 2"})
    assert first.status_code == 200
    assert (await expert.patch(url, json={"unknown": True, "reason": "stamp unreadable"})).status_code == 200
    rows = case.admin.execute("select count(*) from app.availability_overrides where fact_id = %s", (letter,))
    assert rows.fetchone()[0] == 2  # type: ignore[index]
    assert case.audit().count("fact.availability_changed") == 2
    seen = (await expert.get(f"/v1/cases/{case.id}/consultations/{visit}", params={"as_seen": "true"})).json()
    assert [e["fact_id"] for e in seen["unknown"]] == [str(letter)]
    assert seen["visible"] == []


async def test_only_the_named_expert_writes_a_note_on_a_consultation(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    visit = case.fact(_visit_kind(case.pack), date(2024, 5, 15), available=date(2024, 5, 15))
    url = f"/v1/cases/{case.id}/consultations/{visit}/note"
    assert (await (await client_for(case.assistant)).put(url, json={"body": "zz"})).status_code == 403
    expert = await client_for(case.expert)
    assert (await expert.put(url, json={"body": "first"})).status_code == 200
    assert (await expert.put(url, json={"body": "second"})).status_code == 200
    seen = (await expert.get(f"/v1/cases/{case.id}/consultations/{visit}", params={"as_seen": "true"})).json()
    assert seen["note"] == "second"


# --- guidance in force ----------------------------------------------------------------------------


async def test_the_version_in_force_on_a_date_and_none_before_the_first(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed, LAUNCH)
    row = case.pack.guidelines()[0]
    client = await client_for(case.expert)
    on = date.fromisoformat(str(row["in_force_from"]))
    resp = await client.get(
        "/v1/guidelines/in-force", params={"code": row["code"], "date": on.isoformat(), "case_id": str(case.id)}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["code"], body["version_label"], body["url"]) == (row["code"], row["version_label"], row["url"])
    before = await client.get(
        "/v1/guidelines/in-force",
        params={"code": row["code"], "date": date(on.year - 1, 1, 1).isoformat(), "case_id": str(case.id)},
    )
    assert before.status_code == 404 and _code(before) == "not_found"
    outsider = await client_for(case.stranger)
    hidden = await outsider.get(
        "/v1/guidelines/in-force", params={"code": row["code"], "date": on.isoformat(), "case_id": str(case.id)}
    )
    assert hidden.status_code == 404


async def test_a_case_whose_packs_list_no_guidance_finds_none(client_for: ClientFor, seed: Seed) -> None:
    """The fixture packs ship no overlay: nothing is in force, and nothing is guessed."""
    case = Case(seed, FIXTURE)
    assert case.pack.guidelines() == []
    resp = await (await client_for(case.expert)).get(
        "/v1/guidelines/in-force", params={"code": "zz_g1", "date": "2024-05-15", "case_id": str(case.id)}
    )
    assert resp.status_code == 404 and _code(resp) == "not_found"


async def test_launch_guidance_for_the_planted_consultation_date(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed, LAUNCH)
    code = case.pack.guidelines()[0]["code"]
    resp = await (await client_for(case.expert)).get(
        "/v1/guidelines/in-force", params={"code": code, "date": "2024-05-15", "case_id": str(case.id)}
    )
    assert resp.status_code == 200
    expected = in_force(case.pack.guidelines(), code, date(2024, 5, 15))
    assert expected is not None
    assert resp.json()["version_label"] == expected["version_label"]
