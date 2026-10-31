"""Screen 06: what you've been asked (architecture 5.2 and 6.3 T-06, slice C brief, scenarios S4 and S7).

The `extract_questions` job reads the letter of instruction (recorded OCR), takes identifiers out
(rule 7), asks T-06 through the AI kit, and keeps only questions whose cited words are exactly the
letter's words (rules 1 and 2): `evals/.../expected/questions.json` gives four questions and one
assumption, with question 4 usually outside the expertise. A reworded answer is refused. The named
expert edits (the exact words stay), confirms and locks the set; other slices wait for the lock
(`require_questions_locked`). A supplementary letter adds a version; its new question reopens the set
with a warning, and the earlier questions keep their ids, so links to them stay (S7). Notes are drafted
to every current instructing party (S4). Nothing from the letter is printed (rule 6).
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Annotated, Any

import psycopg
import pytest
from fastapi import Depends, FastAPI

from app.core.ai.bedrock import RecordedBedrock
from app.core.ai.kit import AiKit
from app.core.ai.ocr import RecordedOcr
from app.core.ai.recordings import FixtureStore
from app.core.auth.access import CaseAccess
from app.core.contracts.questions import require_questions_locked
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.errors import ProcessingPaused
from app.core.http import router as http_router
from app.core.jobs import JobContext
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from app.features.setup.jobs import extract_questions
from app.features.setup.services import SetupServices, configure
from app.main import mount
from tests.support.ai import cell
from tests.support.db import ClientFor
from tests.support.files import local_storage
from tests.support.packs import LAUNCH
from tests.support.seed import Seed
from tests.support.uploads import complete_and_finalize

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "setup"
EXPECTED = REPO / "evals" / "gb-eaw" / "clin-neg-breach" / "general-practice" / "expected"
QUESTIONS = json.loads((EXPECTED / "questions.json").read_text())
SECOND = json.loads((EXPECTED / "inputs.json").read_text())["instruction_version_2"]
LETTER = (FIXTURES / "letter-of-instruction.pdf").read_bytes()
SUPPLEMENTARY = (FIXTURES / "supplementary-letter-of-instruction.pdf").read_bytes()
CELL = str(LAUNCH.raw("jurisdiction", "pack.yaml")["cell"])
PARTY = "Margaret Ashworth"
IDENTIFIERS = ("Ashworth", "14/03/1969")
PARTY_ROLE = LAUNCH.raw("case-type", "pack.yaml")["party_roles"][0]
IP_TYPE = LAUNCH.raw("case-type", "pack.yaml")["instructing_party_types"][0]


@pytest.fixture
def crypto() -> CaseCrypto:
    return CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    yield store
    await store.aclose()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], storage: CaseFileStorage, crypto: CaseCrypto) -> FastAPI:
    from app.core.auth.supabase import SupabaseAuth
    from app.core.config import auth_settings
    from app.core.email import OutboxMailer
    from app.main import create_app

    built = create_app(
        database=api_db,
        region=CELL,
        auth=SupabaseAuth(auth_settings(auth_env)),
        mailer=OutboxMailer(),
        storage=storage,
        crypto=crypto,
        packs=default_library(),
    )
    probe = http_router(prefix="/v1")

    @probe.get("/cases/{case_id}/probe-questions-locked")
    async def gated(access: Annotated[CaseAccess, Depends(require_questions_locked())]) -> dict[str, bool]:
        return {"ok": True}

    mount(built, probe)
    return built


def services(storage: CaseFileStorage, crypto: CaseCrypto, root: str = "recordings") -> RecordedBedrock:
    settings = cell()
    store = FixtureStore(FIXTURES / root)
    model = RecordedBedrock(settings, store)
    configure(
        SetupServices(
            kit=AiKit(settings, model),
            ocr=RecordedOcr(settings, store),
            storage=storage,
            crypto=crypto,
            packs=default_library(),
        )
    )
    return model


@pytest.fixture
def model(storage: CaseFileStorage, crypto: CaseCrypto) -> Iterator[RecordedBedrock]:
    yield services(storage, crypto)
    configure(None)


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def world(seed: Seed) -> dict[str, uuid.UUID]:
    expert, assistant, viewer, outsider = (seed.person() for _ in range(4))
    org = seed.org(kind="team", members={expert: "expert", assistant: "assistant", viewer: "expert"})
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(
        org, expert, status="active", members={assistant: "assistant", viewer: "viewer"}, packs=LAUNCH.case_row()
    )
    return {"expert": expert, "assistant": assistant, "viewer": viewer, "outsider": outsider, "case": case}


def url(world: dict[str, uuid.UUID], tail: str) -> str:
    return f"/v1/cases/{world['case']}/{tail}"


async def upload(client: Any, case: uuid.UUID, data: bytes, filename: str) -> str:
    body = {"kind": "instructions", "filename": filename, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    started = await client.post(f"/v1/cases/{case}/uploads", json=body)
    assert started.status_code == 201, started.status_code
    upload_id = started.json()["upload_id"]
    put = await client.put(
        f"/v1/uploads/{upload_id}/chunks/1",
        content=data,
        headers={"content-type": "application/octet-stream", "x-chunk-sha256": hashlib.sha256(data).hexdigest()},
    )
    assert put.status_code == 200
    return await complete_and_finalize(client, upload_id, hashlib.sha256(data).hexdigest())


async def add_letter(
    client: Any, world: dict[str, uuid.UUID], data: bytes = LETTER, kind: str = "letter"
) -> dict[str, Any]:
    document = await upload(client, world["case"], data, f"{kind}.pdf")
    added = await client.post(url(world, "instructions"), json={"kind": kind, "document_id": document})
    assert added.status_code == 201, added.status_code
    assert added.json()["job_id"] is not None
    return dict(added.json())


async def run_job(worker_db: Database, world: dict[str, uuid.UUID], job_id: str) -> None:
    await extract_questions(JobContext(worker_db, uuid.UUID(job_id), world["case"], "extract_questions", 1))


async def read_letter(client: Any, world: dict[str, uuid.UUID], worker_db: Database) -> dict[str, Any]:
    party = await client.post(url(world, "parties"), json={"role_key": PARTY_ROLE, "name": PARTY})
    assert party.status_code == 201
    added = await add_letter(client, world)
    await run_job(worker_db, world, added["job_id"])
    return dict((await client.get(url(world, "questions"))).json())


def shape(q: dict[str, Any]) -> dict[str, Any]:
    return {k: q[k] for k in ("number", "text_verbatim", "para_ref", "kind", "outside_expertise")}


# --- reading the letter ----------------------------------------------------------------------------


async def test_the_letter_gives_four_questions_and_one_assumption_in_its_own_words(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    await client.post(url(world, "parties"), json={"role_key": PARTY_ROLE, "name": PARTY})
    added = await add_letter(client, world)
    before = (await client.get(url(world, "questions"))).json()
    assert before["reading"] is True and before["questions"] == []
    assert (await client.post(url(world, "questions/confirm-all"))).status_code == 409

    await run_job(worker_db, world, added["job_id"])
    await run_job(worker_db, world, added["job_id"])  # a retried job adds nothing
    got = (await client.get(url(world, "questions"))).json()
    assert got["reading"] is False and got["locked"] is False and got["rejected"] == 0
    assert [shape(q) for q in got["questions"]] == QUESTIONS["questions"]
    assert [(a["text_verbatim"], a["para_ref"]) for a in got["assumptions"]] == [
        (a["text_verbatim"], a["para_ref"]) for a in QUESTIONS["assumptions"]
    ]
    assert {q["status"] for q in got["questions"] + got["assumptions"]} == {"suggested"}
    assert all(q["instruction_version_id"] == added["id"] for q in got["questions"])
    assert all(q["source_word_ids"] for q in got["questions"])
    # Fix 2: each question remembers which letter it came from
    assert all(q["source_instruction_version_id"] == added["id"] for q in got["questions"])
    assert all(q["source"] == "letter" for q in got["questions"])
    assert all(isinstance(q["page"], int) and q["page"] >= 1 for q in got["questions"])
    assert "setup.questions.warning.outside_expertise" in got["warnings"]


async def test_identifiers_are_out_before_the_model_sees_the_letter(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    await read_letter(await client_for(world["expert"]), world, worker_db)
    assert len(model.requests) == 1
    sent = model.requests[0].text
    assert not [i for i in IDENTIFIERS if i in sent], "an identifier reached the model"


async def test_a_reworded_question_is_refused(
    client_for: ClientFor,
    world: dict[str, uuid.UUID],
    worker_db: Database,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
) -> None:
    services(storage, crypto, root="recordings-paraphrased")
    try:
        got = await read_letter(await client_for(world["expert"]), world, worker_db)
    finally:
        configure(None)
    assert got["rejected"] == 1
    assert "setup.questions.warning.rejected" in got["warnings"]
    expected = {q["text_verbatim"] for q in QUESTIONS["questions"][1:]}
    assert {q["text_verbatim"] for q in got["questions"]} == expected


async def test_without_a_model_the_job_pauses(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, storage: CaseFileStorage
) -> None:
    configure(SetupServices(kit=None, ocr=None, storage=storage, crypto=None, packs=default_library()))
    try:
        client = await client_for(world["expert"])
        added = await add_letter(client, world)
        with pytest.raises(ProcessingPaused):
            await run_job(worker_db, world, added["job_id"])
    finally:
        configure(None)


# --- helpers for confirming each item individually ------------------------------------------------


async def confirm_each(client: Any, world: dict[str, uuid.UUID], got: dict[str, Any]) -> None:
    """Confirm or edit every question and assumption so the set can be locked."""
    for q in got["questions"]:
        if q["status"] == "suggested":
            r = await client.patch(
                url(world, f"questions/{q['id']}"),
                json={"confirm": True},
                headers={"if-match": f'"{q["version"]}"'},
            )
            assert r.status_code == 200, r.text
    for a in got["assumptions"]:
        if a["status"] == "suggested":
            r = await client.patch(
                url(world, f"assumptions/{a['id']}"),
                json={"confirm": True},
                headers={"if-match": f'"{a["version"]}"'},
            )
            assert r.status_code == 200, r.text


# --- editing and confirming -----------------------------------------------------------------------


async def test_an_edit_keeps_the_exact_words_and_only_the_named_expert_edits(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    got = await read_letter(client, world, worker_db)
    q = got["questions"][0]
    path = url(world, f"questions/{q['id']}")
    body = {"text": "My own wording of the first question."}
    assert (await client.patch(path, json=body)).status_code == 428
    assert (await client.patch(path, json=body, headers={"if-match": '"99"'})).status_code == 412
    assistant = await client_for(world["assistant"])
    assert (await assistant.patch(path, json=body, headers={"if-match": f'"{q["version"]}"'})).status_code == 403
    outsider = await client_for(world["outsider"])
    assert (await outsider.patch(path, json=body, headers={"if-match": f'"{q["version"]}"'})).status_code == 404
    assert (await outsider.get(url(world, "questions"))).status_code == 404

    edited = await client.patch(path, json=body, headers={"if-match": f'"{q["version"]}"'})
    assert edited.status_code == 200, edited.status_code
    out = edited.json()
    assert out["status"] == "edited"
    assert out["confirmed_text"] == body["text"]
    assert out["text_verbatim"] == q["text_verbatim"]
    assert edited.headers["etag"] == f'"{out["version"]}"'

    bad_kind = await client.patch(path, json={"kind": "not_a_kind"}, headers={"if-match": edited.headers["etag"]})
    assert bad_kind.status_code == 422
    flagged = await client.patch(path, json={"outside_expertise": True}, headers={"if-match": edited.headers["etag"]})
    assert flagged.status_code == 200 and flagged.json()["outside_expertise"] is True

    second = got["questions"][1]
    confirmed = await client.patch(
        url(world, f"questions/{second['id']}"), json={"confirm": True}, headers={"if-match": f'"{second["version"]}"'}
    )
    assert confirmed.json()["status"] == "confirmed"
    reworded = await client.patch(
        url(world, f"questions/{second['id']}"), json=body, headers={"if-match": confirmed.headers["etag"]}
    )
    assert reworded.status_code == 409

    a = got["assumptions"][0]
    kept = await client.patch(
        url(world, f"assumptions/{a['id']}"), json={"confirm": True}, headers={"if-match": f'"{a["version"]}"'}
    )
    assert kept.status_code == 200 and kept.json()["status"] == "confirmed"
    assert kept.json()["text_verbatim"] == a["text_verbatim"]


async def test_an_edited_question_counts_as_reviewed_and_the_set_can_still_lock(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    """D3: editing a question's wording should not block locking the set."""
    client = await client_for(world["expert"])
    got = await read_letter(client, world, worker_db)

    # Edit the first question's wording
    q = got["questions"][0]
    edited = await client.patch(
        url(world, f"questions/{q['id']}"),
        json={"text": "My own wording."},
        headers={"if-match": f'"{q["version"]}"'},
    )
    assert edited.status_code == 200 and edited.json()["status"] == "edited"

    # confirmed_count must include the edited question
    listing = (await client.get(url(world, "questions"))).json()
    assert listing["confirmed_count"] >= 1  # the edited one counts

    # Confirm the rest individually
    for item in listing["questions"][1:]:
        if item["status"] == "suggested":
            r = await client.patch(
                url(world, f"questions/{item['id']}"),
                json={"confirm": True},
                headers={"if-match": f'"{item["version"]}"'},
            )
            assert r.status_code == 200
    for a in listing["assumptions"]:
        if a["status"] == "suggested":
            r = await client.patch(
                url(world, f"assumptions/{a['id']}"),
                json={"confirm": True},
                headers={"if-match": f'"{a["version"]}"'},
            )
            assert r.status_code == 200

    # Now confirm-all (lock) should succeed
    locked = await client.post(url(world, "questions/confirm-all"))
    assert locked.status_code == 200, locked.text


async def test_confirm_all_refuses_while_any_item_is_still_suggested(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    """D15: the server must not silently bulk-confirm unreviewed items."""
    client = await client_for(world["expert"])
    got = await read_letter(client, world, worker_db)

    # Confirm all but one question
    for q in got["questions"][:-1]:
        await client.patch(
            url(world, f"questions/{q['id']}"),
            json={"confirm": True},
            headers={"if-match": f'"{q["version"]}"'},
        )
    for a in got["assumptions"]:
        await client.patch(
            url(world, f"assumptions/{a['id']}"),
            json={"confirm": True},
            headers={"if-match": f'"{a["version"]}"'},
        )

    # One question is still suggested → confirm-all must refuse
    refused = await client.post(url(world, "questions/confirm-all"))
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "questions_not_reviewed"


async def test_confirm_all_locks_the_set_and_opens_the_gate(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    assert (await client.post(url(world, "questions/confirm-all"))).status_code == 409  # nothing yet
    got = await read_letter(client, world, worker_db)
    gate = await client.get(url(world, "probe-questions-locked"))
    assert gate.status_code == 409 and gate.json()["error"]["code"] == "questions_not_confirmed"
    assistant = await client_for(world["assistant"])
    assert (await assistant.post(url(world, "questions/confirm-all"))).status_code == 403

    # Confirm each item individually first (the server no longer bulk-confirms)
    await confirm_each(client, world, got)

    locked = await client.post(url(world, "questions/confirm-all"))
    assert locked.status_code == 200, locked.status_code
    assert locked.json() == {
        "instruction_version_id": got["instruction_version_id"],
        "locked": True,
        "questions": 4,
        "assumptions": 1,
    }
    assert (await client.get(url(world, "probe-questions-locked"))).status_code == 200
    after = (await client.get(url(world, "questions"))).json()
    assert after["locked"] is True
    assert {q["status"] for q in after["questions"] + after["assumptions"]} == {"confirmed"}
    assert (await client.post(url(world, "questions/confirm-all"))).status_code == 409
    q = after["questions"][0]
    change = await client.patch(
        url(world, f"questions/{q['id']}"), json={"text": "Mine."}, headers={"if-match": f'"{q["version"]}"'}
    )
    assert change.status_code == 409


async def test_a_supplementary_letter_reopens_the_set_and_keeps_the_questions(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    first = await read_letter(client, world, worker_db)
    await confirm_each(client, world, first)
    assert (await client.post(url(world, "questions/confirm-all"))).status_code == 200

    added = await add_letter(client, world, SUPPLEMENTARY, "supplementary")
    assert added["version_no"] == 2 and added["reopens_questions"] is True
    await run_job(worker_db, world, added["job_id"])

    got = (await client.get(url(world, "questions"))).json()
    assert got["version_no"] == 2 and got["locked"] is False and got["reopened"] is True
    assert "setup.questions.warning.reopened" in got["warnings"]
    assert [q["id"] for q in got["questions"][:4]] == [q["id"] for q in first["questions"]]
    new = got["questions"][4]
    assert (new["number"], new["text_verbatim"], new["para_ref"]) == (5, SECOND["new_question"], SECOND["para_ref"])
    assert {q["status"] for q in got["questions"]} == {"suggested"}
    assert {q["instruction_version_id"] for q in got["questions"]} == {added["id"]}
    # Fix 2: old questions keep their source letter even after re-pointing
    old_qs = got["questions"][:4]
    assert all(q["source_instruction_version_id"] == first["instruction_version_id"] for q in old_qs)
    assert got["questions"][4]["source_instruction_version_id"] == added["id"]
    gate = await client.get(url(world, "probe-questions-locked"))
    assert gate.status_code == 409


# --- notes to the instructing parties ---------------------------------------------------------------


async def test_a_note_is_drafted_to_every_current_instructing_party(
    client_for: ClientFor,
    world: dict[str, uuid.UUID],
    worker_db: Database,
    model: RecordedBedrock,
    admin: psycopg.Connection,
) -> None:
    client = await client_for(world["expert"])
    got = await read_letter(client, world, worker_db)
    for firm in ("North Firm", "South Firm", "Old Firm"):
        body = {"type_key": IP_TYPE, "name": "A Person", "firm": firm, "email": "a@example.test"}
        assert (await client.post(url(world, "instructing-parties"), json=body)).status_code == 201
    admin.execute(
        "update app.case_instructing_parties set effective_to = current_date where case_id = %s and firm = 'Old Firm'",
        (world["case"],),
    )

    assistant = await client_for(world["assistant"])
    drafted = await assistant.post(url(world, "instructing-party-notes/draft"), json={"topic": "outside_expertise"})
    assert drafted.status_code == 200, drafted.status_code
    note = drafted.json()
    assert sorted(r["firm"] for r in note["recipients"]) == ["North Firm", "South Firm"]
    assert note["joint"] is True
    assert note["subject_key"] == "setup.notes.outside_expertise.subject"
    outside = [q for q in got["questions"] if q["outside_expertise"]]
    assert [(i["question_id"], i["para_ref"]) for i in note["items"]] == [(q["id"], q["para_ref"]) for q in outside]

    viewer = await client_for(world["viewer"])
    assert (
        await viewer.post(url(world, "instructing-party-notes/draft"), json={"topic": "outside_expertise"})
    ).status_code == 403
    unknown = await client.post(
        url(world, "instructing-party-notes/draft"),
        json={"topic": "questions_reopened", "question_ids": [str(uuid.uuid4())]},
    )
    assert unknown.status_code == 422


# --- who answers which part of a question outside the expert's expertise ----------------------------


async def test_a_question_outside_expertise_keeps_only_the_packs_answer_parts(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    got = await read_letter(client, world, worker_db)
    flagged = [q for q in got["questions"] if q["outside_expertise"]]
    plain = [q for q in got["questions"] if not q["outside_expertise"]]
    assert flagged and plain
    assert all(q["outside_expertise_source"] == "pack" and q["answer_parts"] for q in flagged)
    assert all(q["outside_expertise_source"] is None and q["answer_parts"] is None for q in plain)

    q = plain[0]
    path = url(world, f"questions/{q['id']}")
    marked = await client.patch(path, json={"outside_expertise": True}, headers={"if-match": f'"{q["version"]}"'})
    assert marked.status_code == 200, marked.status_code
    assert marked.json()["outside_expertise_source"] == "expert"
    assert marked.json()["answer_parts"] == flagged[0]["answer_parts"]
    cleared = await client.patch(path, json={"outside_expertise": False}, headers={"if-match": marked.headers["etag"]})
    assert cleared.json()["outside_expertise_source"] is None and cleared.json()["answer_parts"] is None


# --- design match: short titles and file page counts (screens 04 and 06) ----------------------------------


def make_pdf(pages: int) -> bytes:
    from reportlab.pdfgen import canvas

    out = io.BytesIO()
    page = canvas.Canvas(out)
    for n in range(pages):
        page.drawString(72, 700, f"page {n}")
        page.showPage()
    page.save()
    return out.getvalue()


async def test_each_question_has_a_short_label_from_the_model_or_its_own_first_clause(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    got = await read_letter(await client_for(world["expert"]), world, worker_db)
    titles = [q["short_title"] for q in got["questions"]]
    # three come from the model's label; the fourth was left out, so it is the question's first clause
    assert titles == [
        "Standard of care",
        "The May consultation",
        "Urgent referral",
        "If Mrs Ashworth had been referred",
    ]
    assert all(0 < len(t) <= 40 for t in titles)


async def test_the_files_of_a_case_say_how_many_pages_each_pdf_has(
    client_for: ClientFor, world: dict[str, uuid.UUID], model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    records = await upload(client, world["case"], make_pdf(7), "records.pdf")
    note = await upload(client, world["case"], b"PK\x03\x04 not a pdf", "note.docx")
    got = await client.get(url(world, "files"))
    assert got.status_code == 200, got.text
    by_id = {f["document_id"]: f for f in got.json()}
    assert by_id[records]["page_count"] == 7 and by_id[records]["filename"] == "records.pdf"
    assert by_id[note]["page_count"] is None


async def test_only_people_on_the_case_see_its_files(
    client_for: ClientFor, world: dict[str, uuid.UUID], model: RecordedBedrock
) -> None:
    assert (await (await client_for(world["outsider"])).get(url(world, "files"))).status_code == 404


# --- the letter's own pages on screen 06 ---------------------------------------------------------------


async def test_the_letter_pages_are_counted_and_drawn_for_the_case_and_hidden_from_outsiders(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    await read_letter(client, world, worker_db)
    version = (await client.get(url(world, "questions"))).json()["questions"][0]["instruction_version_id"]
    base = url(world, f"instructions/{version}/pages")
    count = await client.get(base)
    assert count.status_code == 200, count.text
    assert count.json()["page_count"] >= 1
    first = await client.get(f"{base}/1")
    assert first.status_code == 200
    assert first.headers["content-type"] == "image/png"
    assert first.content.startswith(b"\x89PNG")
    assert (await client.get(f"{base}/999")).status_code == 404
    assert (await client.get(f"{base}/0")).status_code == 404
    outsider = await client_for(world["outsider"])
    assert (await outsider.get(base)).status_code == 404
    assert (await outsider.get(f"{base}/1")).status_code == 404


# --- adding a question the model missed -----------------------------------------------------------------


async def test_expert_adds_a_question_they_found_in_the_letter(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    got = await read_letter(client, world, worker_db)
    kind = got["questions"][0]["kind"]
    body = {"text": "Did the practice review the results in time?", "para_ref": "5(a)", "kind": kind}
    added = await client.post(url(world, "questions"), json=body)
    assert added.status_code == 201, added.text
    out = added.json()
    assert out["source"] == "expert" and out["source_word_ids"] == [] and out["status"] == "suggested"
    assert out["text_verbatim"] == body["text"] and out["para_ref"] == "5(a)" and out["kind"] == kind
    assert out["number"] == max(q["number"] for q in got["questions"]) + 1
    listed = (await client.get(url(world, "questions"))).json()["questions"]
    assert out["id"] in {q["id"] for q in listed}


async def test_added_question_is_refused_when_set_is_locked(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    got = await read_letter(client, world, worker_db)
    await confirm_each(client, world, got)
    assert (await client.post(url(world, "questions/confirm-all"))).status_code == 200
    body = {"text": "A late question.", "kind": got["questions"][0]["kind"]}
    assert (await client.post(url(world, "questions"), json=body)).status_code == 409


async def test_assistant_cannot_add_a_question(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    got = await read_letter(client, world, worker_db)
    body = {"text": "Not mine to add.", "kind": got["questions"][0]["kind"]}
    assistant = await client_for(world["assistant"])
    assert (await assistant.post(url(world, "questions"), json=body)).status_code == 403
    outsider = await client_for(world["outsider"])
    assert (await outsider.post(url(world, "questions"), json=body)).status_code == 404


async def test_added_question_invalid_kind_is_rejected(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    await read_letter(client, world, worker_db)
    added = await client.post(url(world, "questions"), json={"text": "Why?", "kind": "not_a_real_kind"})
    assert added.status_code == 422
