"""Slice A increment 11c-2: files that are not PDFs (F36, architecture 10.3 "Inputs other than PDF").

`convert.unpack(data, filename)` sniffs one file (`media_type`) and returns its own pages as one PDF
(`pdf`, none when it has no pages), the files inside it (`members`, each to become a document of its
own) and whether it is an exhibit.
- Images (JPEG, PNG, TIFF frames) become image pages; Word files and emails become typed pages whose
  text comes from the file. An email's attachments and a zip's files are members, in name order.
- Audio, video and medical imaging (DICOM) are exhibits, never paged. So is anything unknown, and a
  zip too large to unpack. A PDF passes through.
The contents are made up.
"""

from __future__ import annotations

import io
import wave
import zipfile
from email.message import EmailMessage

import pikepdf
import pytest
from docx import Document
from PIL import Image

from app.core.pdf import embedded_words, split_pages
from app.features.pipeline import convert


def png(colour: str = "white") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (200, 280), colour).save(out, "PNG")
    return out.getvalue()


def jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (200, 280), "grey").save(out, "JPEG")
    return out.getvalue()


def tiff(frames: int) -> bytes:
    out = io.BytesIO()
    first, *rest = [Image.new("RGB", (200, 280), c) for c in ("white", "grey", "black")[:frames]]
    first.save(out, "TIFF", save_all=True, append_images=rest)
    return out.getvalue()


def docx(*paragraphs: str) -> bytes:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def wav() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 800)
    return out.getvalue()


def eml(attachment: bytes | None = None) -> bytes:
    message = EmailMessage()
    message["From"] = "records@example.test"
    message["To"] = "expert@example.test"
    message["Subject"] = "Synthetic further records"
    message["Date"] = "Mon, 05 Oct 2026 09:00:00 +0000"
    message.set_content("Synthetic covering note\nPlease find the letter attached")
    if attachment is not None:
        message.add_attachment(
            attachment,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="referral-letter.docx",
        )
    return message.as_bytes()


def zipped(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return out.getvalue()


def pdf_of(pages: int) -> bytes:
    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page()
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def words(page_pdf: bytes) -> list[str]:
    return [w.text for w in embedded_words(page_pdf).words]


def pages_of(result: convert.Unpacked) -> list[bytes]:
    return split_pages(result.pdf) if result.pdf is not None else []


def test_a_pdf_passes_through() -> None:
    result = convert.unpack(pdf_of(3), "records.pdf")
    assert (result.media_type, len(pages_of(result)), result.members, result.exhibit) == (
        "application/pdf",
        3,
        [],
        False,
    )


@pytest.mark.parametrize(
    ("data", "name", "media", "count"),
    [
        (png(), "photo.png", "image/png", 1),
        (jpeg(), "photographed-page.jpg", "image/jpeg", 1),
        (tiff(2), "scan.tif", "image/tiff", 2),
    ],
)
def test_images_become_image_pages(data: bytes, name: str, media: str, count: int) -> None:
    result = convert.unpack(data, name)
    pages = pages_of(result)
    assert (result.media_type, len(pages), result.exhibit) == (media, count, False)
    assert all(words(p) == [] for p in pages), "an image page should be read by OCR, not typed"


def test_a_word_file_becomes_typed_pages_with_its_own_text() -> None:
    result = convert.unpack(docx("Synthetic referral letter", "Seen in clinic"), "referral-letter.docx")
    (page,) = pages_of(result)
    assert result.media_type == convert.DOCX
    assert words(page) == ["Synthetic", "referral", "letter", "Seen", "in", "clinic"]


def test_a_long_word_file_runs_onto_more_pages() -> None:
    result = convert.unpack(docx(*[f"Synthetic line {n}" for n in range(120)]), "long.docx")
    pages = pages_of(result)
    assert len(pages) >= 2
    assert sum(len(words(p)) for p in pages) == 120 * 3, "text was lost between pages"


def test_an_email_is_one_page_and_its_attachment_a_member() -> None:
    letter = docx("Synthetic referral letter")
    result = convert.unpack(eml(letter), "further-records.eml")
    (page,) = pages_of(result)
    assert result.media_type == "message/rfc822"
    assert {"Synthetic", "further", "records", "covering", "note"} <= set(words(page))
    assert [(m.filename, m.data) for m in result.members] == [("referral-letter.docx", letter)]


def test_a_zip_is_unpacked_into_members_and_has_no_pages_itself() -> None:
    files = {"b/referral-letter.docx": docx("Synthetic letter"), "a/photo.png": png(), "voicemail.wav": wav()}
    result = convert.unpack(zipped(files), "records-batch.zip")
    assert (result.media_type, result.pdf, result.exhibit) == ("application/zip", None, False)
    assert [(m.filename, m.data) for m in result.members] == sorted(files.items())
    assert [convert.sniff(m.data, m.filename) for m in result.members] == ["image/png", convert.DOCX, "audio/wav"]


@pytest.mark.parametrize(
    ("data", "name", "media"),
    [
        (wav(), "voicemail.wav", "audio/wav"),
        (b"\0\0\0\x18ftypisom" + b"\0" * 64, "clip.mp4", "video/mp4"),
        (b"\0" * 128 + b"DICM" + b"\0" * 64, "xray.dcm", "application/dicom"),
        (b"just some bytes nobody knows", "notes.bin", "application/octet-stream"),
    ],
)
def test_files_that_cannot_be_paged_are_exhibits(data: bytes, name: str, media: str) -> None:
    result = convert.unpack(data, name)
    assert (result.media_type, result.pdf, result.members, result.exhibit) == (media, None, [], True)


def test_a_zip_too_large_to_unpack_is_kept_as_an_exhibit() -> None:
    data = zipped({"big.docx": docx("Synthetic letter")})
    result = convert.unpack(data, "batch.zip", limits=convert.Limits(max_bytes=100))
    assert (result.media_type, result.pdf, result.members, result.exhibit) == ("application/zip", None, [], True)
    too_many = zipped({f"{n}.png": png() for n in range(4)})
    assert convert.unpack(too_many, "batch.zip", limits=convert.Limits(max_members=3)).exhibit
