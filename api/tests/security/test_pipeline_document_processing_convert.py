"""Audit 13, DP-03: a Word file, an email or a picture is bounded before and while it is turned into pages.

Everything is synthetic and small: the limits are lowered so nothing large is allocated. No text is printed.
"""

from __future__ import annotations

import io
import textwrap
import zipfile
from email.message import EmailMessage

import pytest
from docx import Document

from app.features.pipeline import convert


def docx_bytes(*paragraphs: str) -> bytes:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def with_part(data: bytes, name: str, body: bytes) -> bytes:
    """The same Word file with one part replaced."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            z.writestr(info.filename, body if info.filename == name else src.read(info))
    return out.getvalue()


def document_xml(inner: bytes) -> bytes:
    return (
        b'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b"<w:body>" + inner + b"</w:body></w:document>"
    )


def paragraphs_xml(text: bytes, count: int) -> bytes:
    return document_xml(b"<w:p><w:r><w:t>" + text + b"</w:t></w:r></w:p>" * count)


def test_the_limits_exist_with_defaults() -> None:
    limits = convert.Limits()
    assert limits.max_convert_bytes > 0 and limits.max_part_bytes > 0 and limits.max_typed_pages >= 1000


def test_a_word_file_whose_main_part_inflates_past_the_cap_is_kept_as_an_exhibit() -> None:
    big = with_part(docx_bytes("x"), "word/document.xml", paragraphs_xml(b"a" * 100, 20_000))  # ~2 MB, tiny packed
    assert len(big) < 50_000, "premise: small on the wire"
    refused = convert.unpack(big, "a.docx", convert.Limits(max_part_bytes=600_000))
    assert refused.exhibit and refused.pdf is None
    control = convert.unpack(docx_bytes("one line"), "b.docx", convert.Limits(max_part_bytes=600_000))
    assert control.pdf is not None and not control.exhibit


def test_a_word_file_that_claims_a_small_part_but_holds_a_large_one_is_not_expanded() -> None:
    """The central directory says the part is small; the bytes are not. The read is bounded by what is really there."""
    big = with_part(docx_bytes("x"), "word/document.xml", paragraphs_xml(b"a" * 100, 20_000))
    liar = understated(big, "word/document.xml", 200)
    found = convert.unpack(liar, "a.docx", convert.Limits(max_part_bytes=600_000))
    assert found.exhibit and found.pdf is None


def understated(data: bytes, name: str, size: int) -> bytes:
    """Rewrites the declared uncompressed size of one entry (central directory and local header)."""
    raw = bytearray(data)
    needle = name.encode()
    at = raw.find(b"PK\x01\x02")
    while at != -1:
        name_len = int.from_bytes(raw[at + 28 : at + 30], "little")
        if bytes(raw[at + 46 : at + 46 + name_len]) == needle:
            raw[at + 24 : at + 28] = size.to_bytes(4, "little")
            local = int.from_bytes(raw[at + 42 : at + 46], "little")
            raw[local + 22 : local + 26] = size.to_bytes(4, "little")
        at = raw.find(b"PK\x01\x02", at + 4)
    return bytes(raw)


def test_an_entity_expansion_payload_in_a_word_file_does_not_expand() -> None:
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
        b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;"><!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">]>'
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r>'
        b"<w:t>&d;</w:t></w:r></w:p></w:body></w:document>"
    )
    found = convert.unpack(with_part(docx_bytes("x"), "word/document.xml", bomb), "a.docx", convert.Limits())
    if found.pdf is None:
        assert found.exhibit
        return
    import pypdfium2 as pdfium

    text = pdfium.PdfDocument(found.pdf)[0].get_textpage().get_text_range()
    assert len(text) < 1000, "the entity was expanded into the page"


def test_a_word_file_with_more_text_than_the_page_cap_is_kept_as_an_exhibit() -> None:
    lines = [f"line {i}" for i in range(400)]  # 400 lines are about 8 pages
    refused = convert.unpack(docx_bytes(*lines), "long.docx", convert.Limits(max_typed_pages=2))
    assert refused.exhibit and refused.pdf is None
    ok = convert.unpack(docx_bytes(*lines[:20]), "short.docx", convert.Limits(max_typed_pages=2))
    assert ok.pdf is not None


def test_one_enormous_paragraph_is_refused_before_it_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapped: list[int] = []
    real = textwrap.wrap

    def counting(text: str, *args: object, **kwargs: object) -> list[str]:
        wrapped.append(len(text))
        return real(text, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(textwrap, "wrap", counting)
    refused = convert.unpack(docx_bytes("word " * 200_000), "wall.docx", convert.Limits(max_typed_pages=2))
    assert refused.exhibit and refused.pdf is None
    assert wrapped == [], "the paragraph was wrapped before the size was checked"


def test_an_email_with_more_text_than_the_page_cap_is_kept_as_an_exhibit() -> None:
    message = EmailMessage()
    message["From"] = "a@example.test"
    message["Subject"] = "Synthetic"
    message.set_content("\n".join(f"line {i}" for i in range(400)))
    refused = convert.unpack(message.as_bytes(), "long.eml", convert.Limits(max_typed_pages=2))
    assert refused.exhibit and refused.pdf is None


def test_a_deeply_nested_email_is_bounded_not_a_crash() -> None:
    depth = 3000
    parts = []
    for i in range(depth):
        parts.append(
            f"Content-Type: multipart/mixed; boundary=b{i}\n\n--b{i}\n"
            f"Content-Type: multipart/mixed; boundary=b{i + 1}\n"
        )
    raw = ("From: a@example.test\nSubject: s\nMIME-Version: 1.0\n" + "\n".join(parts) + "\ntext").encode()
    found = convert.unpack(raw, "deep.eml", convert.Limits())  # must return, not raise or hang
    assert found.exhibit or found.pdf is not None


def test_a_non_pdf_over_the_convert_cap_is_not_converted(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[int] = []
    monkeypatch.setattr(convert, "_docx_lines", lambda data: called.append(1) or [])
    data = docx_bytes("hello")
    found = convert.unpack(data, "a.docx", convert.Limits(max_convert_bytes=len(data) - 1))
    assert found.exhibit and found.pdf is None and called == []
