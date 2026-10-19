"""Screen 13 and comments over HTTP (architecture 5.2 "13 Things to look at", 5.4 comments, slice D brief).

- `GET /findings`: open findings with the pages they rest on and any decision; `gentle` ones say so.
- `PATCH /findings/{id}`: the named expert decides; "not relevant" and "explained elsewhere" need a
  reason (422); every decision is audited; nothing closes a finding on its own.
- `POST /findings/rerun`: the named expert, 202 and a `rerun_checks` job.
- `GET/PATCH /v1/me/checks`: each expert's own settings (on/off, window); with `case_id`, the case's
  pack checks with those settings applied. `POST /v1/me/checks/custom`: see test_understand_design_routes.
- Comments: any reader comments on a page, fact or paragraph; only the author changes the words;
  any reader resolves; strangers get 404.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.core.packs import default_library
from tests.support.db import ClientFor
from tests.support.packs import FIXTURE
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


class Case:
    def __init__(self, seed: Seed) -> None:
        self.seed, self.admin = seed, seed.admin
        self.expert, self.assistant, self.stranger = seed.person(), seed.person(), seed.person()
        org = seed.org(kind="team", members={self.expert: "expert", self.assistant: "assistant"})
        seed.org(kind="personal", members={self.stranger: "owner"})
        self.id = seed.case(org, self.expert, status="active", members={self.assistant: "assistant"})
        self.pack = default_library().combine(*FIXTURE.codes)
        doc = seed.document(self.id)
        self.page_no = 402
        self.page = seed.page(self.id, doc, self.page_no)

    def fact(self) -> uuid.UUID:
        row = self.admin.execute(
            "insert into app.facts (case_id, page_id, kind, value, word_ids, event_date)"
            " values (%s, %s, 'zz', '9', '{w1}', '2024-02-14') returning id",
            (self.id, self.page),
        ).fetchone()
        assert row is not None
        self.admin.execute("update app.facts set status = 'verified' where id = %s", (row[0],))
        return uuid.UUID(str(row[0]))

    def finding(self, *, gentle: bool = False, status: str = "open") -> uuid.UUID:
        check = self.pack.checks()[0]
        row = self.admin.execute(
            "insert into app.findings (case_id, check_key, rule, gentle, evidence_fact_ids, evidence_page_ids,"
            " pack_version) values (%s, %s, %s, %s, %s, %s, 'zz@1') returning id",
            (self.id, check["key"], check["rule"], gentle, [self.fact()], [self.page]),
        ).fetchone()
        assert row is not None
        if status != "open":
            self.admin.execute("update app.findings set status = %s where id = %s", (status, row[0]))
        return uuid.UUID(str(row[0]))

    def audit(self) -> list[str]:
        rows = self.admin.execute("select action from app.audit_log where case_id = %s order by seq", (self.id,))
        return [r[0] for r in rows]


@pytest.fixture
def case(seed: Seed) -> Case:
    return Case(seed)


def _code(resp: Any) -> str:
    return str(resp.json()["error"]["code"])


# --- findings -------------------------------------------------------------------------------------


async def test_findings_show_their_evidence_pages_and_gentle_ones_say_so(client_for: ClientFor, case: Case) -> None:
    plain, gentle = case.finding(), case.finding(gentle=True)
    case.finding(status="gone")
    resp = await (await client_for(case.assistant)).get(f"/v1/cases/{case.id}/findings")
    assert resp.status_code == 200
    got = {f["id"]: f for f in resp.json()["findings"]}
    assert set(got) == {str(plain), str(gentle)}, "a finding no longer found is not listed as open"
    assert got[str(plain)]["evidence_pages"] == [case.page_no]
    assert got[str(plain)]["check_key"] == case.pack.checks()[0]["key"]
    assert (got[str(plain)]["gentle"], got[str(gentle)]["gentle"]) == (False, True)
    assert got[str(plain)]["decision"] is None


async def test_only_the_named_expert_decides_and_some_decisions_need_a_reason(
    client_for: ClientFor, case: Case
) -> None:
    finding = case.finding()
    url = f"/v1/cases/{case.id}/findings/{finding}"
    assistant = await client_for(case.assistant)
    assert (await assistant.patch(url, json={"decision": "relevant"})).status_code == 403
    expert = await client_for(case.expert)
    for decision in ("not_relevant", "explained_elsewhere"):
        resp = await expert.patch(url, json={"decision": decision})
        assert resp.status_code == 422 and _code(resp) == "invalid"
    resp = await expert.patch(url, json={"decision": "explained_elsewhere", "reason": "see page 4"})
    assert resp.status_code == 200
    assert (resp.json()["decision"], resp.json()["reason"]) == ("explained_elsewhere", "see page 4")
    assert "finding.decided" in case.audit()
    assert (await expert.patch(url, json={"decision": "nonsense"})).status_code == 422


async def test_the_named_expert_runs_the_checks_again(client_for: ClientFor, case: Case) -> None:
    url = f"/v1/cases/{case.id}/findings/rerun"
    assert (await (await client_for(case.assistant)).post(url)).status_code == 403
    resp = await (await client_for(case.expert)).post(url)
    assert resp.status_code == 202 and resp.json()["job_id"]
    rows = case.admin.execute("select count(*) from app.jobs where case_id = %s and kind = 'rerun_checks'", (case.id,))
    assert rows.fetchone()[0] == 1  # type: ignore[index]


# --- check settings -------------------------------------------------------------------------------


async def test_check_settings_are_the_expert_s_own_and_shape_the_case_s_checks(
    client_for: ClientFor, case: Case
) -> None:
    first = case.pack.checks()[0]["key"]
    expert = await client_for(case.expert)
    listed = (await expert.get("/v1/me/checks", params={"case_id": str(case.id)})).json()["checks"]
    assert [c["key"] for c in listed] == [c["key"] for c in case.pack.checks()]
    assert all(c["enabled"] is (c.get("default_on", True) is not False) for c in listed)
    resp = await expert.patch("/v1/me/checks", json={"check_key": first, "enabled": False, "window_days": 30})
    assert resp.status_code == 200
    listed = (await expert.get("/v1/me/checks", params={"case_id": str(case.id)})).json()["checks"]
    mine = next(c for c in listed if c["key"] == first)
    assert (mine["enabled"], mine["window_days"]) == (False, 30)
    other = await client_for(case.assistant)
    theirs = (await other.get("/v1/me/checks")).json()["settings"]
    assert theirs == []
    assert (await expert.patch("/v1/me/checks", json={"check_key": first, "window_days": 0})).status_code == 422


async def test_a_case_s_checks_are_hidden_from_strangers(client_for: ClientFor, case: Case) -> None:
    resp = await (await client_for(case.stranger)).get("/v1/me/checks", params={"case_id": str(case.id)})
    assert resp.status_code == 404


# --- comments -------------------------------------------------------------------------------------


async def test_any_reader_comments_only_the_author_edits_and_any_reader_resolves(
    client_for: ClientFor, case: Case
) -> None:
    url = f"/v1/cases/{case.id}/comments"
    assistant, expert = await client_for(case.assistant), await client_for(case.expert)
    resp = await assistant.post(url, json={"target_kind": "page", "target_id": str(case.page), "body": "look here"})
    assert resp.status_code == 201, resp.text
    comment = resp.json()
    listed = (await expert.get(url, params={"target_kind": "page", "target_id": str(case.page)})).json()["comments"]
    assert [c["id"] for c in listed] == [comment["id"]]
    headers = {"If-Match": f'"{comment["version"]}"'}
    one = f"{url}/{comment['id']}"
    refused = await expert.patch(one, json={"body": "changed"}, headers=headers)
    assert refused.status_code == 403
    edited = await assistant.patch(one, json={"body": "look again"}, headers=headers)
    assert edited.status_code == 200 and edited.json()["body"] == "look again"
    resolved = await expert.patch(one, json={"resolved": True}, headers={"If-Match": edited.headers["etag"]})
    assert resolved.status_code == 200 and resolved.json()["resolved_at"]
    assert (
        await assistant.post(url, json={"target_kind": "nowhere", "target_id": "x", "body": "x"})
    ).status_code == 422


async def test_strangers_see_no_comments(client_for: ClientFor, case: Case) -> None:
    resp = await (await client_for(case.stranger)).get(f"/v1/cases/{case.id}/comments")
    assert resp.status_code == 404
