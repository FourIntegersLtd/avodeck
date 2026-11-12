"""The parser calls the pipeline runs in killable child processes (audit 13, DP-09; `app.core.sandbox`).

Every function here is importable at the top level, takes only picklable arguments (paths, bytes, numbers,
dataclasses) and returns only picklable results, because it runs in a child of the sandbox pool that shares nothing
with the worker. They are the only places the pipeline hands a stranger's bytes to pikepdf, PDFium or Pillow.
The service looks them up on this module at call time (`sandbox.run(parsing.cut_pages, ...)`), so a test can swap
one for a stand-in that hangs or exits. Nothing here logs; an error carries a class and no text (rule 6).
"""

from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf

from app.core.ai.ocr import OcrPage
from app.core.config import document_limits
from app.core.pdf import cut_page
from app.features.pipeline import convert, duplicates, render, split
from app.features.pipeline.render import Pictures
from app.features.pipeline.words import usable_text_layer


class TooManyPages(Exception):
    """A document has more pages than `MAX_DOCUMENT_PAGES` (or would take the bundle past `MAX_BUNDLE_PAGES`)."""


def checked_pages(source: Path | str | bytes, max_pages: int | None = None) -> int:
    """The page count of a PDF, after cutting every page once and dropping it, so a damaged page stops the
    document here (F2), as it always did, not part-way through the pictures.

    The reader is told not to repair (`attempt_recovery=False`): a cut-off file, or one whose cross-reference
    table is wrong, raises `PdfError` instead of being silently rebuilt with pages lost (V4d). A PDF with no
    pages raises too (V4e), so either way the file is listed as damaged and the expert is told. A PDF over
    `max_pages` (default `MAX_DOCUMENT_PAGES`) raises `TooManyPages` on the count alone, before any page is cut."""
    cap = document_limits().max_document_pages if max_pages is None else max_pages
    with pikepdf.open(io.BytesIO(source) if isinstance(source, bytes) else source, attempt_recovery=False) as pdf:
        count = len(pdf.pages)
        if count == 0:
            raise pikepdf.PdfError("no pages")
        if count > cap:
            raise TooManyPages
        for index in range(count):
            cut_page(pdf, index)
        return count


def count_pages(source: str | bytes, max_pages: int) -> int:
    return checked_pages(source, max_pages)


def declared_pages(path: str) -> int | None:
    """The page count the PDF itself states in its page tree (PV-11), or None when it states none. A file cut off
    after a page was lost still says how many pages it started with; `count_pages` counts the ones that are there."""
    with pikepdf.open(path, attempt_recovery=False) as pdf:
        stated = pdf.Root.Pages.get("/Count")
        return int(stated) if stated is not None else None


def cut_pages(path: str, indexes: list[int]) -> list[bytes]:
    """Pages `indexes` (0-based) of the PDF at `path`, each as its own PDF, with the reader that counted them."""
    with pikepdf.open(path, attempt_recovery=False) as pdf:
        return [cut_page(pdf, index) for index in indexes]


def halves(page_pdf: bytes, axis: str, at: float, first: str) -> bytes:
    """The two-page cut of one stored page (the expert's split request), made in a sandbox child."""
    return split.halves(page_pdf, axis, at, first)


def picture_hash(thumbnail: bytes) -> int:
    """The duplicate check's perceptual hash of a stored thumbnail (a decode of bytes drawn from the document)."""
    return duplicates.picture_hash(thumbnail)


def unpack(data: bytes, filename: str, limits: convert.Limits) -> convert.Unpacked:
    return convert.unpack(data, filename, limits)


@dataclass(frozen=True)
class FileMember:
    """A file found inside an email or a zip, left as a file in the caller's scratch directory."""

    filename: str
    path: str
    size: int
    sha256: str
    media_type: str


@dataclass(frozen=True)
class UnpackedFiles:
    """`convert.Unpacked` with the bytes left on disk: only paths, sizes and hashes cross the pipe."""

    media_type: str
    pdf_path: str | None = None
    members: list[FileMember] = field(default_factory=list)
    exhibit: bool = False


def _write(directory: str, name: str, data: bytes) -> str:
    path = os.path.join(directory, name)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return path


def unpack_file(path: str, filename: str, limits: convert.Limits, out_dir: str) -> UnpackedFiles:
    """`unpack` of the file at `path`, read here and not sent through the pipe; the converted PDF and each member
    are written to `out_dir` (the caller's private scratch directory) and come back as paths."""
    found = convert.unpack(Path(path).read_bytes(), filename, limits)
    pdf_path = _write(out_dir, "pdf", found.pdf) if found.pdf is not None else None
    members = []
    for n, member in enumerate(found.members, start=1):
        members.append(
            FileMember(
                filename=member.filename,
                path=_write(out_dir, f"member-{n}", member.data),
                size=len(member.data),
                sha256=hashlib.sha256(member.data).hexdigest(),
                media_type=convert.sniff(member.data, member.filename),
            )
        )
    return UnpackedFiles(found.media_type, pdf_path, members, found.exhibit)


def text_layer(page_pdf: bytes) -> OcrPage | None:
    return usable_text_layer(page_pdf)


def pictures(page_pdf: bytes, rotation: int) -> Pictures:
    return render.render(page_pdf, rotation)
