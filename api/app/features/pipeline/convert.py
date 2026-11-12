"""Files that are not PDFs (F36, architecture 10.3 "Inputs other than PDF").

`unpack` sniffs one file by its bytes (the filename only tells an email apart) and returns its own
pages as one PDF, the files inside it (each becomes a document of its own) and whether it is an
exhibit. Images become image pages, read by OCR as usual. Word files and emails are typed onto
pages whose words come from the file itself. Audio, video, medical imaging, anything unknown and
archives too large to unpack are exhibits: kept, listed, never paged. Nothing here is logged.
"""

from __future__ import annotations

import io
import re
import textwrap
import warnings
import zipfile
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

import pikepdf
from PIL import Image

from app.core.config import DocumentLimits
from app.core.pdf import is_pdf

PDF = "application/pdf"
ZIP = "application/zip"
EMAIL = "message/rfc822"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
UNKNOWN = "application/octet-stream"

PAGED = {PDF, EMAIL, DOCX, "image/jpeg", "image/png", "image/tiff"}

A4 = (595, 842)
MARGIN = 72
FONT_SIZE = 11
LEADING = 14
LINE_CHARS = 84
IMAGE_DPI = 200

_HEADER = re.compile(rb"^(From|To|Subject|Date|MIME-Version|Received|Return-Path|Message-ID):", re.I | re.M)


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 500 * 1024 * 1024  # what a zip may unpack to
    max_members: int = 2000
    max_depth: int = 3  # zips and emails inside each other
    max_image_pixels: int = 100_000_000  # one frame, width x height
    max_image_frames: int = 2000  # pages of one multi-frame picture (a TIFF)
    max_image_total_pixels: int = 1_000_000_000  # every frame of one picture together
    max_convert_bytes: int = 100 * 1024 * 1024  # a Word file, email or picture read whole to be turned into pages
    max_part_bytes: int = 64 * 1024 * 1024  # what one part of a Word file may inflate to
    max_typed_pages: int = 4000  # pages of typed text one Word file or email may make
    max_document_pages: int = 4000  # pages one document may have (`MAX_DOCUMENT_PAGES`)
    max_bundle_pages: int = 4000  # pages one bundle may have (`MAX_BUNDLE_PAGES`)


def limits_from(settings: DocumentLimits) -> Limits:
    """The processing limits with the cell's document settings applied."""
    return Limits(
        max_image_pixels=settings.max_image_pixels,
        max_image_frames=settings.max_image_frames,
        max_convert_bytes=settings.max_convert_bytes,
        max_typed_pages=settings.max_document_pages,
        max_document_pages=settings.max_document_pages,
        max_bundle_pages=settings.max_bundle_pages,
    )


@dataclass(frozen=True)
class Member:
    filename: str
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class Unpacked:
    media_type: str
    pdf: bytes | None = field(default=None, repr=False)
    members: list[Member] = field(default_factory=list)
    exhibit: bool = False


def sniff(data: bytes, filename: str = "") -> str:
    head = data[:64]
    if data.startswith(b"%PDF-"):
        return PDF
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if head.startswith(b"PK\x03\x04"):
        return _zip_kind(io.BytesIO(data))
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "audio/wav"
    if head.startswith(b"RIFF") and head[8:12] == b"AVI ":
        return "video/x-msvideo"
    if head.startswith((b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xe3")):
        return "audio/mpeg"
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if head.startswith(b"fLaC"):
        return "audio/flac"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "video/x-matroska"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"heif"):
            return "image/heic"
        if brand.startswith(b"M4A"):
            return "audio/mp4"
        if brand == b"qt  ":
            return "video/quicktime"
        return "video/mp4"
    if data[128:132] == b"DICM":
        return "application/dicom"
    if is_pdf(data):  # a PDF with a byte-order mark or a lead-in before its marker
        return PDF
    if filename.lower().endswith(".eml") or _HEADER.match(data[:2048]):
        return EMAIL
    return UNKNOWN


def _zip_kind(source: str | Path | io.BytesIO) -> str:
    try:
        with zipfile.ZipFile(source) as z:
            names = set(z.namelist())
    except zipfile.BadZipFile:
        return UNKNOWN
    if "word/document.xml" in names:
        return DOCX
    if "xl/workbook.xml" in names:
        return XLSX
    return PPTX if "ppt/presentation.xml" in names else ZIP


def sniff_path(path: Path, filename: str = "") -> str:
    """`sniff` for a file on disk: only its first bytes are read (a zip's names come from its directory), so a
    large file's type is known without holding it in memory."""
    with path.open("rb") as handle:
        head = handle.read(2048)
    if head.startswith(b"PK\x03\x04"):
        return _zip_kind(path)
    return sniff(head, filename)


def unpack(data: bytes, filename: str, limits: Limits | None = None) -> Unpacked:
    limits = limits or Limits()
    media = sniff(data, filename)
    if media == PDF:
        return Unpacked(media, pdf=data)
    if media not in (ZIP,) and len(data) > limits.max_convert_bytes:
        return Unpacked(media, exhibit=True)  # too big to hold and convert; kept whole like any file we cannot page
    if media == ZIP:
        members = _zip_members(data, limits)
        return Unpacked(media, members=members) if members is not None else Unpacked(media, exhibit=True)
    if media not in PAGED:
        return Unpacked(media, exhibit=True)
    try:
        if media == DOCX:
            return Unpacked(media, pdf=_typed(_docx_lines(data, limits), limits))
        if media == EMAIL:
            lines, members = _email(data)
            return Unpacked(media, pdf=_typed(lines, limits), members=members)
        return Unpacked(media, pdf=_image_pages(data, limits))
    except Exception:  # noqa: BLE001 - a file we cannot turn into pages is kept as an exhibit
        return Unpacked(media, exhibit=True)


# --- archives ------------------------------------------------------------------------------------


def _zip_members(data: bytes, limits: Limits) -> list[Member] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = [i for i in z.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX/")]
            if len(infos) > limits.max_members or sum(i.file_size for i in infos) > limits.max_bytes:
                return None
            if any(i.flag_bits & 0x1 for i in infos):
                return None  # locked with a password
            return sorted((Member(i.filename, z.read(i)) for i in infos), key=lambda m: m.filename)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, ValueError):
        return None


# --- emails --------------------------------------------------------------------------------------


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in ("br", "p", "div", "tr", "li"):
            self.parts.append("\n")


def _email(data: bytes) -> tuple[list[str], list[Member]]:
    message = BytesParser(policy=policy.default).parsebytes(data)
    assert isinstance(message, EmailMessage)
    lines = [f"{h}: {message[h]}" for h in ("From", "To", "Cc", "Date", "Subject") if message[h]]
    lines.append("")
    body = message.get_body(preferencelist=("plain", "html"))
    if body is not None:
        text = str(body.get_content())
        if body.get_content_type() == "text/html":
            reader = _Text()
            reader.feed(text)
            text = "".join(reader.parts)
        lines.extend(text.splitlines())
    members: list[Member] = []
    for n, part in enumerate(message.iter_attachments(), start=1):
        name = part.get_filename() or f"attachment-{n}"
        if part.get_content_type() == EMAIL:
            inner = part.get_content()
            payload = inner.as_bytes() if isinstance(inner, EmailMessage) else bytes(inner)
        else:
            raw = part.get_payload(decode=True)
            payload = raw if isinstance(raw, bytes) else b""
        members.append(Member(name, bytes(payload)))
    return lines, members


# --- Word files ----------------------------------------------------------------------------------


def _check_parts(data: bytes, limits: Limits) -> None:
    """Refuses a Word file any part of which inflates past `max_part_bytes` (or all of which pass `max_bytes`).
    The directory's declared sizes are checked first, then every part is inflated in blocks and counted, so a
    directory that understates its parts cannot let more through; python-docx reads every part when it opens."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = z.infolist()
        if sum(i.file_size for i in infos) > limits.max_bytes or any(
            i.file_size > limits.max_part_bytes for i in infos
        ):
            raise ValueError("part too large")
        total = 0
        for info in infos:
            with z.open(info) as part:
                size = 0
                while block := part.read(1024 * 1024):
                    size += len(block)
                    total += len(block)
                    if size > limits.max_part_bytes or total > limits.max_bytes:
                        raise ValueError("part too large")


def _docx_lines(data: bytes, limits: Limits) -> list[str]:
    from docx import Document

    _check_parts(data, limits)
    document = Document(io.BytesIO(data))
    lines = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            lines.append("  ".join(cell.text for cell in row.cells))
    return lines


# --- writing pages -------------------------------------------------------------------------------


def _save(pdf: pikepdf.Pdf) -> bytes:
    out = io.BytesIO()
    pdf.save(out, deterministic_id=True)
    return out.getvalue()


def _escape(line: str) -> bytes:
    raw = line.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _typed(lines: list[str], limits: Limits | None = None) -> bytes:
    """Text typed in Helvetica onto A4 pages, wrapped, so its words come from the text layer. More text than
    `max_typed_pages` pages raises, checked before a line is wrapped (its non-blank characters alone need that
    many lines) and again as the lines are made."""
    per_page = (A4[1] - 2 * MARGIN) // LEADING
    cap = (limits or Limits()).max_typed_pages * per_page  # lines
    if len(lines) > cap or sum(len(line) - line.count(" ") for line in lines) > cap * LINE_CHARS:
        raise ValueError("too much text")
    wrapped: list[str] = []
    for line in lines:
        clean = line.replace("\t", "    ").rstrip()
        wrapped.extend(textwrap.wrap(clean, LINE_CHARS) or [""])
        if len(wrapped) > cap:
            raise ValueError("too much text")
    chunks = [wrapped[i : i + per_page] for i in range(0, len(wrapped), per_page)] or [[]]
    pdf = pikepdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding,
        )
    )
    for chunk in chunks:
        ops = b"".join(
            b"BT /F1 %d Tf %d %d Td (%s) Tj ET\n" % (FONT_SIZE, MARGIN, A4[1] - MARGIN - LEADING * i, _escape(t))
            for i, t in enumerate(chunk)
            if t.strip()
        )
        pdf.pages.append(
            pikepdf.Page(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Page,
                    MediaBox=[0, 0, *A4],
                    Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
                    Contents=pdf.make_stream(ops),
                )
            )
        )
    return _save(pdf)


def _image_pages(data: bytes, limits: Limits | None = None) -> bytes:
    """One image-only page per image frame, for OCR to read.

    The declared sizes are checked first (the frame count, each frame's pixels, all frames together) and a file over
    any limit raises, so nothing is decoded; a Pillow decompression-bomb warning is an error here too. The frames are
    then decoded one at a time into a one-page PDF each, so only one decoded frame is in memory at once."""
    limits = limits or Limits()
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            count = getattr(image, "n_frames", 1)
            if count > limits.max_image_frames:
                raise ValueError("too many frames")
            total = 0
            for n in range(count):
                image.seek(n)
                pixels = image.size[0] * image.size[1]
                total += pixels
                if pixels > limits.max_image_pixels or total > limits.max_image_total_pixels:
                    raise ValueError("too many pixels")
            merged = pikepdf.new()
            for n in range(count):
                image.seek(n)
                single = io.BytesIO()
                image.convert("RGB").save(single, "PDF", resolution=IMAGE_DPI)
                with pikepdf.open(single) as one:
                    merged.pages.extend(one.pages)
    merged.docinfo = merged.make_indirect(pikepdf.Dictionary())
    return _save(merged)
