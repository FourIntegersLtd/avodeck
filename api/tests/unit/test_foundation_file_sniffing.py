"""A file's kind is read from its first bytes: a PDF starts with its marker, a zip that merely holds one is a zip.

Every file here is made up.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.core.pdf import is_pdf
from app.features.pipeline import convert

PDF_BYTES = b"%PDF-1.7 synthetic"


def zipped(members: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return out.getvalue()


def test_a_zip_that_holds_a_pdf_marker_early_on_is_a_zip() -> None:
    data = zipped({"a.txt": PDF_BYTES})
    assert PDF_BYTES in data[:1024]
    assert convert.sniff(data) == convert.ZIP
    assert not is_pdf(data)


def test_a_spreadsheet_is_not_unpacked_as_a_bundle_zip() -> None:
    data = zipped({"[Content_Types].xml": b"<x/>", "xl/workbook.xml": b"<x/>"})
    assert convert.sniff(data) == convert.XLSX
    assert not convert.unpack(data, "sheet.xlsx").members
    assert convert.unpack(data, "sheet.xlsx").exhibit


@pytest.mark.parametrize("head", [b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xe3", b"ID3"])
def test_mp3_frame_headers_are_recognised(head: bytes) -> None:
    assert convert.sniff(head + b"\x90\x00" * 40) == "audio/mpeg"


def test_a_pdf_starts_with_its_marker() -> None:
    assert is_pdf(PDF_BYTES)
    assert convert.sniff(PDF_BYTES) == convert.PDF
    assert not is_pdf(b"")


@pytest.mark.parametrize("lead", [b"\xef\xbb\xbf", b"\r\n", b"junk bytes\n" * 20])
def test_a_pdf_with_a_byte_order_mark_or_leading_junk_is_still_a_pdf(lead: bytes) -> None:
    data = lead + PDF_BYTES
    assert is_pdf(data)
    assert convert.sniff(data) == convert.PDF


def test_the_marker_far_into_a_file_is_not_a_pdf() -> None:
    assert not is_pdf(b"x" * 2000 + PDF_BYTES)


def test_a_zip_is_never_a_pdf_whatever_it_holds() -> None:
    assert not is_pdf(b"PK\x03\x04" + b"\x00" * 10 + PDF_BYTES)
