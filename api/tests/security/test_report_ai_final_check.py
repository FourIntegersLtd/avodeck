# ruff: noqa: F811  (pytest fixtures imported from the slice F sign tests)
"""AI audit, step 14: slice F's one AI task, T-17 (tone and expertise warnings), inside the final check.

The final check gates signing and serving, so the question is whether anything the model says can change what
the check decides. It cannot: every check is code, and T-17 only points at words already in the expert's own
paragraphs. The model is a scripted fake, so "the injection worked" is modelled as the hostile ANSWER a steered
model would give; each test says what the server then stores, shows and does.

Each test states the SAFE behaviour. Where the application is not safe, the safe assertion is kept and the test
is marked `xfail(strict=True)` with the finding id, so a fix turns it into a failure to remove the mark.
Every "blocked" assertion has a positive control (the legitimate answer or the legitimate path works).

Ids: P prompt injection, E exfiltration, O output validation, I integrity, A authority, C cost.
All text is synthetic; nothing here is a real record, and no report text is printed on failure.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.core.ai.bedrock import ModelRequest, ModelResponse
from app.core.ai.kit import AiKit
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.storage import CaseFileStorage
from app.features.report import ai_tasks
from app.features.report.assemble import PARAGRAPH_PURPOSE
from app.features.report.jobs import final_check
from app.features.report.services import ReportServices, configure
from tests.fixtures.report.world import (
    CLEAN,
    CRYPTO,
    UNCITED_DATE,
    Reads,
    World,
    body_paragraphs,
    make_ready,
    save_opinion,
    store_signature,
)
from tests.http.test_report_build import latest_docx
from tests.http.test_report_sign import (  # noqa: F401  (fixtures and helpers)
    SIGN,
    aconn,
    app,
    code,
    newest,
    reads,
    ready,
    served_body,
    services,
    signed,
    steps,
    storage,
    worker_db,
    world,
)
from tests.support.ai import CELL_ENV, cell
from tests.support.ai_injection import CANARY, INJECTIONS
from tests.support.contracts import add_fact, add_question
from tests.support.db import ClientFor
from tests.support.identity import SENTENCE, leaked, record_identity
from tests.support.packs import LAUNCH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

INJECTION_NAMES = list(INJECTIONS)
FOREIGN = "Zorblaxmarker"
MARKUP = "<img src=x onerror=alert(1)> and <script>alert(2)</script> **bold** [link](javascript:alert(3))"
REPORT = Path(__file__).resolve().parents[2] / "app" / "features" / "report"
WEB = Path(__file__).resolve().parents[3] / "web" / "src" / "capabilities" / "report"
LINE = re.compile(r"(?m)^sentence\t(\d+)\t(.*)$")


@pytest.fixture
def combo() -> Combo:
    return LAUNCH


# --- helpers --------------------------------------------------------------------------------------------


class Steer:
    """The model: `answer(request)` gives the answer (a dict) or an exception to raise. Keeps every request."""

    def __init__(self, answer: Callable[[ModelRequest], dict[str, Any] | Exception]) -> None:
        self.answer = answer
        self.requests: list[ModelRequest] = []
        self.endpoint = CELL_ENV["BEDROCK_ENDPOINT"]

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        out = self.answer(request)
        if isinstance(out, Exception):
            raise out
        return ModelResponse(output=out, input_tokens=100, output_tokens=10)


def use(model: Steer, reads: Reads, storage: CaseFileStorage, *, kit: bool = True) -> Steer:
    configure(ReportServices(crypto=CRYPTO, storage=storage, reads=reads, kit=AiKit(cell(), model) if kit else None))
    return model


def sentences(request: ModelRequest) -> dict[int, str]:
    return {int(n): text for n, text in LINE.findall(request.text)}


def quiet(request: ModelRequest) -> dict[str, Any]:
    return {"issues": []}


def flag_all(request: ModelRequest) -> dict[str, Any]:
    """A model that flags the first two words of every sentence (words really in the paragraph)."""
    issues = [
        {"sentence_index": n, "type": "loaded_language", "words": " ".join(text.split()[:2])}
        for n, text in sentences(request).items()
    ]
    return {"issues": issues}


def flag(marker: str, kind: str = "loaded_language") -> Callable[[ModelRequest], dict[str, Any]]:
    """A model that flags exactly the words `marker` in the sentence that holds them."""

    def answer(request: ModelRequest) -> dict[str, Any]:
        found = [n for n, text in sentences(request).items() if marker in text]
        assert found, "the marker is not in any sentence the model was shown"
        return {"issues": [{"sentence_index": found[0], "type": kind, "words": marker}]}

    return answer


def snapshot(admin: psycopg.Connection, w: World) -> dict[str, Any]:
    """Everything a final check (and so a model's answer) must not be able to change."""

    def rows(sql: str) -> list[tuple[Any, ...]]:
        return [tuple(r) for r in admin.execute(sql, (w.case,)).fetchall()]

    return {
        "paragraphs": rows(
            "select id, para_no, section, text from app.report_paragraphs where case_id = %s order by id"
        ),
        "citations": rows(
            "select id, fact_id, fact_version, value_snapshot, quote_verbatim from app.citations"
            " where case_id = %s order by id"
        ),
        "versions": rows(
            "select id, version_no, type, status, locked_at, sha256, docx_path, pdf_path"
            " from app.report_versions where case_id = %s order by id"
        ),
        "builds": rows(
            "select report_version_id, page_count, pack_versions::text from app.report_builds"
            " where case_id = %s order by 1"
        ),
        "opinions": rows("select id, body, citation_refs::text from app.opinions where case_id = %s order by id"),
        "sections": rows("select section, body from app.report_section_texts where case_id = %s order by section"),
        "facts": rows("select id, status, value, version from app.facts where case_id = %s order by id"),
        "case": rows("select status, on_hold from app.cases where id = %s"),
        "audit": rows("select action from app.audit_log where case_id = %s order by seq"),
    }


async def prepared(
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    w: World,
    reads: Reads,
    storage: CaseFileStorage,
    steps: dict[str, Callable[[], Any]],
    **kwargs: Any,
) -> None:
    await make_ready(seed, admin, aconn, w, reads, **kwargs)
    await store_signature(storage, admin, w)
    await steps["build"]()


def run_rows(admin: psycopg.Connection, w: World) -> list[tuple[Any, ...]]:
    return [
        tuple(r)
        for r in admin.execute(
            "select passed, warnings_status, warnings::text, results::text from app.final_check_runs"
            " where case_id = %s order by at, created_at",
            (w.case,),
        ).fetchall()
    ]


def outcomes(run: dict[str, Any]) -> dict[str, str]:
    return {r["key"]: r["outcome"] for r in run["results"]}


# --- request side ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", INJECTION_NAMES)
async def test_p_a_hostile_sentence_in_the_report_reaches_the_text_of_t17_and_never_the_system_prompt(
    name: str,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    steps: dict[str, Callable[[], Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    await prepared(seed, admin, aconn, world, reads, storage, steps, body=f"{CLEAN}\n\n{INJECTIONS[name]}")
    model = use(Steer(quiet), reads, storage)

    run = await steps["check"]()

    assert len(model.requests) == 1, "one T-17 call per final check"
    request = model.requests[0]
    assert request.task_id == "T-17"
    assert CANARY in request.text, "the hostile sentence did not reach the model text, so nothing was tested"
    assert CANARY not in request.system
    assert CANARY not in str(request.context)
    assert CANARY not in str(request.schema)
    assert CANARY not in request.model_id
    assert CANARY not in caplog.text, "report words are never logged (rule 6)"
    assert run["warnings_status"] == "done" and run["warnings"] == []
    assert all(CANARY not in text for *_, text in [r[2:] for r in run_rows(admin, world)])


async def test_e_a_hostile_cited_fact_never_reaches_the_model_only_the_expert_paragraphs_do(
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    steps: dict[str, Callable[[], Any]],
) -> None:
    hostile = f"Ferritin 9 ug/L {INJECTIONS['disclose_other_documents']}"
    admin.execute("update app.facts set value = %s where id = %s", (hostile, world.result))
    await prepared(seed, admin, aconn, world, reads, storage, steps)
    model = use(Steer(quiet), reads, storage)

    run = await steps["check"]()

    quotes = admin.execute("select count(*) from app.citations where case_id = %s", (world.case,)).fetchone()
    assert quotes == (2,), "the citation to the hostile fact stands, so the case really carries it"
    assert model.requests and "result was low" in model.requests[0].text, "control: the paragraphs were sent"
    for request in model.requests:
        assert CANARY not in request.text, "a cited value or quote is never sent to T-17"
        assert CANARY not in request.system and CANARY not in str(request.context)
    assert run["warnings_status"] == "done"


async def test_e_identifiers_are_stripped_before_t17_whatever_the_sentence_around_them_says(
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    steps: dict[str, Callable[[], Any]],
) -> None:
    await record_identity(aconn, CRYPTO, world.case)
    body = f"{CLEAN}\n\n{SENTENCE} {INJECTIONS['ignore_instructions']}"
    await prepared(seed, admin, aconn, world, reads, storage, steps, body=body)
    model = use(Steer(quiet), reads, storage)

    await steps["check"]()

    sent = [r.text for r in model.requests]
    assert sent and CANARY in sent[0], "control: the hostile sentence was sent"
    assert leaked(sent) == []


async def test_e_nothing_from_another_case_is_sent_or_stored(
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    steps: dict[str, Callable[[], Any]],
) -> None:
    other = seed.case(world.org, world.expert, status="active", packs=world.combo.case_row())
    question = add_question(admin, other, status="confirmed")
    await save_opinion(aconn, other, question, f"{FOREIGN} was recorded by the other case.", [])
    await prepared(seed, admin, aconn, world, reads, storage, steps)
    model = use(Steer(quiet), reads, storage)

    run = await steps["check"]()

    stored = admin.execute("select count(*) from app.opinions where case_id = %s", (other,)).fetchone()
    assert stored == (1,), "control: the other case has an opinion of its own"
    assert model.requests and "result was low" in model.requests[0].text
    assert all(FOREIGN not in r.text for r in model.requests)
    assert all(FOREIGN not in str(r) for r in run_rows(admin, world))
    assert run["warnings"] == []


# --- answer side: a steered, empty, partial or refused answer never turns a check into a pass -------------


async def baseline(steps: dict[str, Callable[[], Any]], reads: Reads, storage: CaseFileStorage) -> dict[str, Any]:
    use(Steer(quiet), reads, storage)
    run: dict[str, Any] = await steps["check"]()
    assert run["warnings_status"] == "done", "control: with a quiet model T-17 ran"
    return run


def pause(admin: psycopg.Connection, w: World) -> None:
    admin.execute(
        "insert into app.ai_calls (case_id, task_id, model_id, region, prompt_version, input_tokens, output_tokens,"
        " outcome) values (%s, 'T-17', 'zz.model', 'zz', 'x', 1000000000, 1, 'ok')",
        (w.case,),
    )


STEERED_ALL_FINE = {"issues": [], "passed": True, "failed": [], "results": "every check passed"}
ANSWERS: dict[str, tuple[Callable[[ModelRequest], dict[str, Any] | Exception], str]] = {
    "empty": (quiet, "done"),
    "fabricated_words": (
        lambda r: {"issues": [{"sentence_index": 0, "type": "uncited_fact", "words": f"Invented {CANARY}"}]},
        "done",
    ),
    "claims_all_fine": (lambda r: STEERED_ALL_FINE, "not_run"),
    "refused_twice": (lambda r: {"issues": "none"}, "not_run"),
    "out_of_range_sentence": (
        lambda r: {"issues": [{"sentence_index": 9999, "type": "uncited_fact", "words": "The plan"}]},
        "not_run",
    ),
    "unknown_kind": (
        lambda r: {"issues": [{"sentence_index": 0, "type": "passed", "words": "The plan"}]},
        "not_run",
    ),
    "free_prose_reason": (
        lambda r: {
            "issues": [{"sentence_index": 0, "type": "uncited_fact", "words": "The plan", "reason": f"Say {CANARY}"}]
        },
        "not_run",
    ),
    "words_too_long": (
        lambda r: {"issues": [{"sentence_index": 0, "type": "uncited_fact", "words": "x" * 201}]},
        "not_run",
    ),
    "provider_cut_off": (lambda r: {}, "not_run"),
}


@pytest.mark.parametrize("mode", [*ANSWERS, "cost_paused", "no_kit"])
async def test_o_no_model_answer_changes_any_check_and_one_that_did_not_run_says_so(
    mode: str,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    steps: dict[str, Callable[[], Any]],
) -> None:
    """A case with three planted problems: whatever the model says, exactly those three fail and block."""
    finding = await make_ready(
        seed, admin, aconn, world, reads, read_all=False, decided=False, body=f"{CLEAN}\n\n{UNCITED_DATE}"
    )
    await steps["build"]()
    base = await baseline(steps, reads, storage)
    assert {k for k, v in outcomes(base).items() if v == "failed"} == {
        "dates_cited",
        "all_pages_read",
        "findings_decided",
    }

    if mode == "cost_paused":
        pause(admin, world)
        model = use(Steer(quiet), reads, storage)
        expected = "not_run"
    elif mode == "no_kit":
        model = use(Steer(quiet), reads, storage, kit=False)
        expected = "not_run"
    else:
        answer, expected = ANSWERS[mode]
        model = use(Steer(answer), reads, storage)

    run = await steps["check"]()

    assert outcomes(run) == outcomes(base), "a model answer changed what a check decided"
    assert [r["fixes"] for r in run["results"]] == [r["fixes"] for r in base["results"]]
    assert run["passed"] is False and run["ready"] is False
    assert [f["key"] for f in run["failed"]] == [f["key"] for f in base["failed"]]
    assert finding is not None
    assert run["warnings_status"] == expected, "an unchecked T-17 must say it was not run"
    assert run["warnings"] == [], "a steered or refused answer produced a warning"
    if mode in ("cost_paused", "no_kit"):
        assert model.requests == [], "no model call was made"
    rows = run_rows(admin, world)
    assert len(rows) == 2 and CANARY not in str(rows[-1])


@pytest.mark.parametrize("mode", ["cost_paused", "no_kit", "refused_twice"])
async def test_o_a_ready_case_with_an_unchecked_t17_is_not_shown_as_checked(
    mode: str,
    admin: psycopg.Connection,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    steps: dict[str, Callable[[], Any]],
    ready: World,
) -> None:
    """T-17 warns and never blocks (brief), so the code checks still pass; but 'not run' is stored and sent."""
    if mode == "cost_paused":
        pause(admin, world)
        use(Steer(quiet), reads, storage)
    elif mode == "no_kit":
        use(Steer(quiet), reads, storage, kit=False)
    else:
        use(Steer(lambda r: {"issues": "none"}), reads, storage)

    run = await steps["check"]()

    assert run["passed"] is True and run["warnings"] == []
    assert run["warnings_status"] == "not_run"
    assert admin.execute("select warnings_status from app.final_check_runs where id = %s", (run["id"],)).fetchone() == (
        "not_run",
    )
    # control: the same case with a model that answers (and a budget left) is "done"
    admin.execute("delete from app.ai_calls where case_id = %s and model_id = 'zz.model'", (world.case,))
    use(Steer(quiet), reads, storage)
    assert (await steps["check"]())["warnings_status"] == "done"


async def test_o_the_screen_says_when_the_tone_check_could_not_run() -> None:
    """The API sends `warnings_status`; the screen must not read 'not run' as 'nothing to warn about'."""
    source = (WEB / "FinalCheck.tsx").read_text()
    assert re.search(r'warnings_status\s*===\s*"not_run"', source), "S17 never shows a T-17 that did not run"


async def test_o_only_real_words_of_the_experts_own_paragraphs_become_warnings(
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    steps: dict[str, Callable[[], Any]],
) -> None:
    await prepared(seed, admin, aconn, world, reads, storage, steps)
    declared = admin.execute(
        "select p.id, p.text from app.report_paragraphs p join app.report_versions v on v.id = p.report_version_id"
        " where p.case_id = %s and p.section = 'declarations' order by v.version_no desc, p.para_no limit 1",
        (world.case,),
    ).fetchone()
    assert declared is not None
    declaration = (await CRYPTO.decrypt(aconn, world.case, bytes(declared[1]), purpose=PARAGRAPH_PURPOSE)).decode()
    own = " ".join(declaration.split()[:3])

    def answer(request: ModelRequest) -> dict[str, Any]:
        lines = sentences(request)
        assert own not in " ".join(lines.values()), "the declaration wording is not the expert's to be checked"
        plan = next(n for n, text in lines.items() if "The plan" in text)
        other = next(n for n, text in lines.items() if "The plan" not in text)
        return {
            "issues": [
                {"sentence_index": plan, "type": "outside_expertise", "words": own},  # words of another paragraph
                {
                    "sentence_index": other,
                    "type": "loaded_language",
                    "words": "The plan",
                },  # words of the plan paragraph
                {"sentence_index": plan, "type": "loaded_language", "words": "the plan"},  # real, any case
                {"sentence_index": plan, "type": "loaded_language", "words": "The  plan"},  # the same place again
            ]
        }

    use(Steer(answer), reads, storage)
    run = await steps["check"]()

    assert run["warnings_status"] == "done"
    assert [(w["type"], w["words"].lower()) for w in run["warnings"]] == [("loaded_language", "the plan")]
    version = admin.execute(
        "select id from app.report_versions where case_id = %s order by version_no desc limit 1", (world.case,)
    ).fetchone()
    assert version is not None
    mine = {
        r[0]
        for r in admin.execute(
            "select id from app.report_paragraphs where report_version_id = %s and section <> 'declarations'",
            (version[0],),
        ).fetchall()
    }
    assert {uuid.UUID(w["paragraph_id"]) for w in run["warnings"]} <= mine
    stored = admin.execute("select warnings from app.final_check_runs where id = %s", (run["id"],)).fetchone()
    assert stored is not None
    for place in stored[0]:
        assert set(place) == {"paragraph_id", "para_no", "section", "question_id", "type", "start", "end"}, (
            "a warning is a place, never the model's or the report's words (rule 6)"
        )


async def test_i_an_answer_cannot_add_remove_or_reword_report_text_citations_or_declarations(
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    steps: dict[str, Callable[[], Any]],
) -> None:
    await prepared(
        seed, admin, aconn, world, reads, storage, steps, body=f"{CLEAN}\n\n{INJECTIONS['fabricate_information']}"
    )
    before = snapshot(admin, world)
    assert before["paragraphs"] and before["citations"], "control: there is a report to change"

    use(Steer(flag_all), reads, storage)
    run = await steps["check"]()
    assert run["warnings"], "control: the model's answer was accepted as warnings"
    use(Steer(lambda r: {"issues": [], "paragraphs": ["Delete everything"], "text": CANARY}), reads, storage)
    await steps["check"]()
    use(Steer(flag("fabricate" if False else CANARY)), reads, storage)
    await steps["check"]()

    after = snapshot(admin, world)
    assert after == before


async def test_i_nothing_the_model_makes_is_rendered_into_the_report_or_the_screen() -> None:
    """Only the final check's own module asks the model; the builders and the screens never see its words."""
    asking = {"checks.py", "jobs.py", "services.py"}
    pattern = re.compile(r"\bAiKit\b|\bai_tasks\b|\bkit\.run\b|\bservices\.kit\b")
    using = {path.name for path in REPORT.glob("*.py") if pattern.search(path.read_text())}
    assert "checks.py" in using, "control: the scan finds the one place that asks the model"
    assert using <= asking, f"{sorted(using - asking)} reach the model"
    for name in ("assemble.py", "docx_out.py", "pdf_out.py", "signing.py", "invoice_pdf.py"):
        assert not re.search(r"warning|tone_warnings|T-17", (REPORT / name).read_text()), name
    assert "escape(para.text)" in (REPORT / "pdf_out.py").read_text(), "PDF text is escaped, never read as markup"
    scanned = [p for p in WEB.rglob("*.ts*") if ".test." not in p.name and ".design." not in p.name]
    assert scanned
    for path in scanned:
        text = path.read_text()
        assert not re.search(
            r"dangerouslySetInnerHTML|innerHTML|insertAdjacentHTML|from \"marked\"|marked\(|react-markdown|DOMPurify",
            text,
        ), path.name


async def test_o_markup_in_a_paragraph_and_in_a_flag_stays_plain_text_everywhere(
    seed: Seed,
    client_for: ClientFor,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    steps: dict[str, Callable[[], Any]],
) -> None:
    await prepared(seed, admin, aconn, world, reads, storage, steps, body=f"{CLEAN}\n\n{MARKUP}")
    use(Steer(flag(MARKUP)), reads, storage)

    run = await steps["check"]()

    assert [w["words"] for w in run["warnings"]] == [MARKUP], "the words stay a plain string"
    resp = await (await client_for(world.expert)).get(world.url("report/final-check/latest"))
    assert resp.headers["content-type"].startswith("application/json")
    from app.features.report.pdf_out import page_count

    version = newest(admin, world.case)
    pdf = await (await client_for(world.expert)).get(world.url(f"report/versions/{version['id']}/download?format=pdf"))
    assert pdf.status_code == 200 and page_count(pdf.content) >= 1, "the PDF builds with markup in the text"
    paragraphs = " ".join(text for _, text, _ in body_paragraphs(await latest_docx(admin, storage, aconn, world)))
    assert "<script>" in paragraphs and "<img" in paragraphs, "the Word file holds the text, not interpreted markup"


# --- authority ------------------------------------------------------------------------------------------


async def test_a_a_steered_answer_cannot_sign_serve_send_or_change_a_status(
    client_for: ClientFor,
    admin: psycopg.Connection,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    steps: dict[str, Callable[[], Any]],
    ready: World,
) -> None:
    before = snapshot(admin, world)
    kinds = admin.execute(
        "select kind, count(*) from app.jobs where case_id = %s group by kind order by kind", (world.case,)
    ).fetchall()
    use(Steer(flag_all), reads, storage)
    run = await steps["check"]()
    assert run["passed"] is True and run["warnings"], "control: a full, passing check with the model's warnings"

    assert snapshot(admin, world) == before, "no status, version, citation or audit row changed"
    after = admin.execute(
        "select kind, count(*) from app.jobs where case_id = %s group by kind order by kind", (world.case,)
    ).fetchall()
    assert {k: n for k, n in after if k != "final_check"} == {k: n for k, n in kinds if k != "final_check"}
    assert admin.execute("select count(*) from app.report_servings where case_id = %s", (world.case,)).fetchone() == (
        0,
    )
    assert newest(admin, world.case)["status"] == "draft"


async def test_a_signing_and_serving_still_need_step_up_and_the_named_expert_after_a_clean_check(
    client_for: ClientFor,
    admin: psycopg.Connection,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    steps: dict[str, Callable[[], Any]],
    ready: World,
) -> None:
    from datetime import timedelta

    use(Steer(quiet), reads, storage)
    run = await steps["check"]()
    assert run["passed"] is True and run["ready"] is True
    stale = await client_for(world.expert, mfa_age=timedelta(minutes=15))
    refused = await stale.post(world.url("report/sign"), json=SIGN)
    assert refused.status_code == 403 and code(refused) == "step_up_required"
    for person in (world.co_expert, world.assistant, world.viewer):
        assert (await (await client_for(person)).post(world.url("report/sign"), json=SIGN)).status_code == 403
    version = await signed(client_for, world)  # control: the named expert with fresh MFA signs
    url = world.url(f"report/versions/{version}/serve")
    assert (await stale.post(url, json=served_body())).status_code == 403
    assert (await (await client_for(world.co_expert)).post(url, json=served_body())).status_code == 403
    assert (await (await client_for(world.expert)).post(url, json=served_body())).status_code == 200


async def test_a_a_model_that_says_all_clear_cannot_open_the_gate_to_signing(
    client_for: ClientFor,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    steps: dict[str, Callable[[], Any]],
) -> None:
    await prepared(seed, admin, aconn, world, reads, storage, steps, read_all=False, decided=False)
    use(Steer(lambda r: STEERED_ALL_FINE), reads, storage)
    run = await steps["check"]()
    assert run["passed"] is False

    resp = await (await client_for(world.expert)).post(world.url("report/sign"), json=SIGN)

    assert resp.status_code == 409 and code(resp) == "final_check_failed"
    assert newest(admin, world.case)["status"] == "draft"


async def test_a_only_verified_facts_are_cited_in_the_built_report_whatever_the_model_says(
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    steps: dict[str, Callable[[], Any]],
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    suggested = add_fact(admin, world.case, world.pages[1], status="suggested", value="Ferritin 12 ug/L")
    from tests.fixtures.report.world import cite, ready_refs

    refs = [*ready_refs(world), cite(suggested, world.pages[1], "Ferritin 12")]
    await save_opinion(aconn, world.case, world.question, f"{CLEAN} It was also low [[cite:3]].", refs)
    await store_signature(storage, admin, world)
    await steps["build"]()
    use(Steer(lambda r: STEERED_ALL_FINE), reads, storage)

    run = await steps["check"]()

    cited = {
        r[0] for r in admin.execute("select fact_id from app.citations where case_id = %s", (world.case,)).fetchall()
    }
    assert world.plan in cited and world.result in cited, "control: verified facts are cited"
    assert suggested not in cited, "a fact that is not verified is never cited (rule 2)"
    assert outcomes(run)["citations_resolve"] == "failed" and run["passed"] is False


async def test_a_a_served_version_stays_locked_when_a_later_ai_job_runs(
    client_for: ClientFor,
    admin: psycopg.Connection,
    worker_db: Database,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    steps: dict[str, Callable[[], Any]],
    ready: World,
) -> None:
    """Rule 8: serving locks the version; a final-check job afterwards neither calls the model nor touches it."""
    version = await signed(client_for, world)
    client = await client_for(world.expert)
    assert (await client.post(world.url(f"report/versions/{version}/serve"), json=served_body())).status_code == 200
    before = snapshot(admin, world)
    model = use(Steer(flag_all), reads, storage)

    queued = await client.post(world.url("report/final-check"))
    assert queued.status_code == 409 and code(queued) == "not_built"
    queued_id = admin.execute(
        "insert into app.jobs (case_id, kind, priority) values (%s, 'final_check', 0) returning id", (world.case,)
    ).fetchone()
    assert queued_id is not None
    await final_check(JobContext(worker_db, queued_id[0], world.case, "final_check", 1))  # the job still runs

    assert model.requests == [], "no draft: nothing was sent to the model"
    assert snapshot(admin, world) == before
    served = admin.execute("select status from app.report_versions where id = %s", (version,)).fetchone()
    assert served == ("served",)
    refused = psycopg.errors.Error
    with pytest.raises(refused):
        admin.execute("update app.report_versions set display_name = 'Changed' where id = %s", (version,))


# --- cost -----------------------------------------------------------------------------------------------


async def test_c_the_model_calling_site_passes_the_case_so_the_cost_guard_applies() -> None:
    source = (REPORT / "checks.py").read_text()
    start = source.index("await kit.run(")
    call = source[start : source.index("\n        )", start)]
    assert "case_id=i.case_id" in call
    assert "ProcessingPaused" in source


async def test_c_a_loop_of_final_check_requests_queues_one_job_not_one_model_call_each(
    client_for: ClientFor,
    admin: psycopg.Connection,
    worker_db: Database,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    ready: World,
) -> None:
    client = await client_for(world.expert)
    ids = {(await client.post(world.url("report/final-check"))).json()["job_id"] for _ in range(8)}
    waiting = admin.execute(
        "select count(*) from app.jobs where case_id = %s and kind = 'final_check' and status in ('queued', 'running')",
        (world.case,),
    ).fetchone()
    assert waiting == (1,) and len(ids) == 1, "eight clicks queued more than one T-17 call"
    # control: once that job has run, asking again is a new job (a real second check)
    use(Steer(quiet), reads, storage)
    await final_check(JobContext(worker_db, uuid.UUID(ids.pop()), world.case, "final_check", 1))
    again = await client.post(world.url("report/final-check"))
    assert again.status_code == 202


async def test_o_t17_schema_is_bounded() -> None:
    from app.core.packs import default_library

    pack = default_library().combine(*LAUNCH.codes)
    schema = ai_tasks.t17(pack, 5).schema
    issues = schema["properties"]["issues"]
    item = issues["items"]
    assert (
        issues["maxItems"] == 15 and schema["additionalProperties"] is False and item["additionalProperties"] is False
    )
    assert item["properties"]["words"]["maxLength"] == 200
    assert item["properties"]["sentence_index"]["maximum"] == 4
    assert item["properties"]["type"]["enum"] == list(ai_tasks.KINDS)
    assert set(item["properties"]) == {"sentence_index", "type", "words"}, "no free-prose field (rule 1)"
