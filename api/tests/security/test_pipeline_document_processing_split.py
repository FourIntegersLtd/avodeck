"""Audit 13, DP-03: the split step does not read a large non-PDF whole, and a file inside an email or archive can
never stop the job through its name. Files are made up here; nothing printed is patient text."""

from __future__ import annotations

import io
from email.message import EmailMessage
from pathlib import Path

import pytest
from docx import Document

from app.features.pipeline import convert
from app.features.pipeline import service as pipeline_service
from tests.repository.test_pipeline_support import Harness, text_pdf
from tests.security import test_foundation_file_lifecycle_validation as lifecycle
from tests.support.file_lifecycle_documents import zip_of

pytestmark = pytest.mark.anyio

h = lifecycle.h  # the same harness fixtures
storage = lifecycle.storage
all_documents, run = lifecycle.all_documents, lifecycle.run


def docx_bytes(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def exhibits(h: Harness) -> list[str]:
    rows = h.admin.execute(
        "select d.filename from app.exhibits e join app.documents d on d.id = e.document_id"
        " where e.bundle_id = (select bundle_id from app.cases where id = %s) order by 1",
        (h.case_id,),
    ).fetchall()
    return [r[0] for r in rows]


async def test_a_non_pdf_over_the_convert_cap_is_never_read_into_memory_and_the_rest_goes_on(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    big = docx_bytes("synthetic note")
    cap = convert.Limits(max_convert_bytes=len(big) - 1)
    monkeypatch.setattr(pipeline_service, "LIMITS", cap)
    converted: list[int] = []
    real_unpack = convert.unpack
    monkeypatch.setattr(convert, "unpack", lambda *a, **k: converted.append(1) or real_unpack(*a, **k))
    read: list[int] = []
    real_read = Path.read_bytes

    def counting(self: Path) -> bytes:
        data = real_read(self)
        read.append(len(data))
        return data

    monkeypatch.setattr(Path, "read_bytes", counting)

    await run(h, big, "note.docx")
    await run(h, text_pdf([["synthetic one"]]), "plain.pdf")
    assert exhibits(h) == ["note.docx"], "the Word file is kept whole and listed, like any file we cannot page"
    assert len(h.pages()) == 1, "the other file is numbered as usual"
    assert converted == [] and not any(n >= len(big) for n in read), "the file was read whole and converted"


def email_with_attachment_named(name: str) -> bytes:
    message = EmailMessage()
    message["From"] = "a@example.test"
    message["Subject"] = "Synthetic"
    message.set_content("covering note")
    message.add_attachment(text_pdf([["synthetic inner"]]), maintype="application", subtype="pdf", filename=name)
    return message.as_bytes()


@pytest.mark.parametrize("name", ["a\x00b.pdf", "\x00", "line\nbreak.pdf", "x" * 400 + ".pdf"], ids=lambda n: repr(n[:8]))
async def test_a_name_inside_an_email_or_zip_that_postgres_or_the_column_cannot_hold_does_not_stop_the_job(
    h: Harness, name: str
) -> None:
    inner = 1
    if "\n" not in name:  # an email header cannot carry a line break at all
        await run(h, email_with_attachment_named(name), "mail.eml")
        inner += 1
    await run(h, zip_of({name: text_pdf([["synthetic zipped"]])}), "pack.zip")
    names = [d["filename"] for d in all_documents(h)]
    assert all(1 <= len(n) <= 255 for n in names)
    assert not any(any(ord(c) < 32 or ord(c) == 127 for c in n) for n in names), "a control character was stored"
    assert len(h.pages()) == inner + (inner - 1), "every inner PDF (and the email's body page) is numbered"
