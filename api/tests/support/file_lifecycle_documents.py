"""Synthetic hostile and ordinary files for the file-lifecycle audit tests (step 12).

Everything is built in code from a handful of lines, nothing is checked in, and nothing is about anyone:
pages say "synthetic page N". The EICAR antivirus test string is assembled from two halves so that this
source file is not itself flagged by a scanner.
"""

from __future__ import annotations

import hashlib
import io
import struct
import zipfile
import zlib
from collections.abc import Mapping
from email.message import EmailMessage
from typing import Any

import httpx
import pikepdf
from PIL import Image

from app.core.crypto import CaseCrypto
from tests.support.files import CHUNK

_EICAR_A = "X5O!P%@AP[4\\PZX54(P^)7CC)7}$"
_EICAR_B = "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

A4 = (595, 842)


def eicar() -> bytes:
    return (_EICAR_A + _EICAR_B).encode()


# --- PDFs ------------------------------------------------------------------------------------------


def _save(pdf: pikepdf.Pdf, **kwargs: Any) -> bytes:
    out = io.BytesIO()
    pdf.save(out, **kwargs)
    return out.getvalue()


def _typed_page(pdf: pikepdf.Pdf, text: str, **extra: Any) -> pikepdf.Page:
    font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    )
    page = pikepdf.Page(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Page,
            MediaBox=[0, 0, *A4],
            Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
            Contents=pdf.make_stream(f"BT /F1 12 Tf 72 760 Td ({text}) Tj ET\n".encode()),
            **extra,
        )
    )
    pdf.pages.append(page)
    return page


def text_pdf(pages: int = 1, label: str = "synthetic page") -> bytes:
    """A born-digital PDF with a text layer, so no OCR is needed to read it."""
    pdf = pikepdf.new()
    for n in range(1, pages + 1):
        _typed_page(pdf, f"{label} {n}")
    return _save(pdf)


def pdf_with_eicar(pages: int = 1) -> bytes:
    """A valid PDF with the EICAR test string after its end marker (a reader ignores it; a scanner sees it)."""
    return text_pdf(pages) + b"\n%" + eicar() + b"\n"


def encrypted_pdf(password: str = "synthetic-pw") -> bytes:
    """A PDF that needs a password to open."""
    pdf = pikepdf.new()
    _typed_page(pdf, "synthetic page 1")
    return _save(pdf, encryption=pikepdf.Encryption(user=password, owner=password + "-owner"))


def truncated_pdf(pages: int = 3, keep: float = 0.4, *, surviving: int | None = None) -> bytes:
    """A PDF cut off after `keep` of its bytes. With `surviving`, the helper checks (with the repairing reader the
    application uses) how many pages are still found in the cut file, so a test never rests on a guess."""
    data = text_pdf(pages)
    cut = data[: int(len(data) * keep)]
    if surviving is not None:
        with pikepdf.open(io.BytesIO(cut)) as reopened:
            found = len(reopened.pages)
        assert found == surviving, f"premise: cutting {pages} pages at {keep} leaves {found} pages, not {surviving}"
    return cut


def garbage_after_pdf_marker() -> bytes:
    return b"%PDF-1.7\n" + bytes(range(256)) * 8


ACTIVE_MARKERS = (b"/JavaScript", b"/JS", b"/OpenAction", b"/Launch", b"/EmbeddedFile", b"/GoToR", b"/AA")


def active_content_pdf() -> bytes:
    """One page carrying every kind of active or outward-reaching content a PDF can hold.

    Document level: an /OpenAction that runs JavaScript, a JavaScript name tree, an embedded file.
    Page level: an /AA (additional actions) that runs JavaScript and three link annotations (a /Launch action,
    an external /URI and a /GoToR to another file).
    """
    pdf = pikepdf.new()
    js = pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String("app.alert('synthetic-open');"))
    page_js = pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String("app.alert('synthetic-page');"))
    links = [
        pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=pikepdf.Name.Link,
            Rect=[10, 10, 100, 40],
            A=pikepdf.Dictionary(S=pikepdf.Name.Launch, F=pikepdf.String("synthetic-launch.exe")),
        ),
        pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=pikepdf.Name.Link,
            Rect=[10, 50, 100, 80],
            A=pikepdf.Dictionary(S=pikepdf.Name.URI, URI=pikepdf.String("https://example.invalid/synthetic")),
        ),
        pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=pikepdf.Name.Link,
            Rect=[10, 90, 100, 120],
            A=pikepdf.Dictionary(
                S=pikepdf.Name.GoToR, F=pikepdf.String("synthetic-other.pdf"), D=pikepdf.Array([0, pikepdf.Name.Fit])
            ),
        ),
    ]
    _typed_page(
        pdf,
        "synthetic page 1",
        Annots=pikepdf.Array([pdf.make_indirect(link) for link in links]),
        AA=pikepdf.Dictionary(O=page_js),
    )
    pdf.Root.OpenAction = pdf.make_indirect(js)
    pdf.Root.Names = pikepdf.Dictionary(
        JavaScript=pikepdf.Dictionary(Names=pikepdf.Array([pikepdf.String("synthetic"), pdf.make_indirect(js)]))
    )
    pdf.attachments["synthetic.txt"] = pikepdf.AttachedFileSpec(
        pdf,
        b"synthetic embedded file",
        description="",
        filename="synthetic.txt",
        mime_type="text/plain",
        creation_date="",
        mod_date="",
    )
    return _save(pdf)


def blank_pages_pdf(count: int) -> bytes:
    pdf = pikepdf.new()
    for _ in range(count):
        pdf.add_blank_page(page_size=(72, 72))
    return _save(pdf)


def huge_mediabox_pdf(points: int = 14400) -> bytes:
    """One empty page of 200 x 200 inches (the largest a PDF viewer is meant to honour)."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(points, points))
    return _save(pdf)


# --- images ----------------------------------------------------------------------------------------


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png_declaring(width: int, height: int) -> bytes:
    """A PNG whose header claims `width` x `height` pixels and whose data is a few bytes long."""
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + _png_chunk(b"IEND", b"")
    )


def real_image(fmt: str = "PNG", size: tuple[int, int] = (16, 16)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (200, 200, 200)).save(out, fmt)
    return out.getvalue()


def tiff_frames(count: int) -> bytes:
    out = io.BytesIO()
    frames = [Image.new("L", (1, 1), n % 256) for n in range(count)]
    frames[0].save(out, "TIFF", save_all=True, append_images=frames[1:])
    return out.getvalue()


# --- zips and emails -------------------------------------------------------------------------------


def zip_of(members: Mapping[str, bytes], *, deflate: bool = True) -> bytes:
    out = io.BytesIO()
    kind = zipfile.ZIP_DEFLATED if deflate else zipfile.ZIP_STORED
    with zipfile.ZipFile(out, "w", kind) as z:
        for name, data in members.items():
            z.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), data, compress_type=kind)
    return out.getvalue()


def nested_zip(depth: int, leaf: bytes, leaf_name: str = "leaf.pdf") -> bytes:
    """`depth` zips inside one another (depth 1 is a plain zip holding `leaf`)."""
    data = zip_of({leaf_name: leaf})
    for n in range(depth - 1):
        data = zip_of({f"level-{n}.zip": data})
    return data


def zip_in_email_in_zip(leaf: bytes) -> bytes:
    inner = zip_of({"leaf.pdf": leaf})
    message = EmailMessage()
    message["From"] = "a@example.invalid"
    message["To"] = "b@example.invalid"
    message["Subject"] = "synthetic"
    message.set_content("synthetic body")
    message.add_attachment(inner, maintype="application", subtype="zip", filename="inner.zip")
    return zip_of({"mail.eml": message.as_bytes()})


def _patch_zip(data: bytes, *, size: int | None = None, crc: int | None = None, flag: int | None = None) -> bytes:
    """Rewrite the size, CRC and/or flag bits of every member in both its local and central headers."""
    buf = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        offsets = [i.header_offset for i in z.infolist()]
    for at in offsets:
        assert buf[at : at + 4] == b"PK\x03\x04"
        if flag is not None:
            buf[at + 6 : at + 8] = struct.pack("<H", struct.unpack("<H", buf[at + 6 : at + 8])[0] | flag)
        if crc is not None:
            buf[at + 14 : at + 18] = struct.pack("<I", crc)
        if size is not None:
            buf[at + 22 : at + 26] = struct.pack("<I", size)
    at = 0
    while (at := bytes(buf).find(b"PK\x01\x02", at)) != -1:
        if flag is not None:
            buf[at + 8 : at + 10] = struct.pack("<H", struct.unpack("<H", buf[at + 8 : at + 10])[0] | flag)
        if crc is not None:
            buf[at + 16 : at + 20] = struct.pack("<I", crc)
        if size is not None:
            buf[at + 24 : at + 28] = struct.pack("<I", size)
        at += 4
    return bytes(buf)


def password_flagged_zip(members: Mapping[str, bytes]) -> bytes:
    """A zip whose members are marked as encrypted (general-purpose bit 0), as a password-locked one is."""
    return _patch_zip(zip_of(members), flag=0x1)


def forged_size_zip(real_size: int, declared: int, *, honest_crc_of_prefix: bool = False) -> bytes:
    """One deflated member that inflates to `real_size` zero bytes while its headers claim `declared` bytes.

    With `honest_crc_of_prefix` the CRC is the one of the first `declared` bytes, so a reader that stops at the
    declared size sees a perfectly consistent member.
    """
    data = zip_of({"forged.bin": b"\0" * real_size})
    crc = zlib.crc32(b"\0" * declared) if honest_crc_of_prefix else None
    return _patch_zip(data, size=declared, crc=crc)


def declared_size_zip(real: bytes, declared: int) -> bytes:
    return _patch_zip(zip_of({"claimed.bin": real}), size=declared)


def pdf_then_zip() -> bytes:
    """Valid as a PDF (marker first) and as a zip (the zip's directory sits at the end of the file)."""
    return text_pdf(1) + zip_of({"inside.txt": b"synthetic"})


def pdf_with_html_tail() -> bytes:
    """A valid PDF with an HTML page (and a script) appended: both a PDF reader and a browser find something."""
    return text_pdf(1) + b"\n<html><body><script>/* synthetic */</script></body></html>\n"


def png_with_zip_tail() -> bytes:
    """A real picture followed by a zip (the zip's directory is at the end, so a zip reader opens it)."""
    return real_image("PNG", (16, 16)) + zip_of({"inside.txt": b"synthetic"})


def zip_with_pdf_marker_first_member() -> bytes:
    """A zip whose first stored member starts with a PDF marker well inside the first 1024 bytes."""
    return zip_of({"inside.pdf": text_pdf(1)}, deflate=False)


# --- driving the pipeline --------------------------------------------------------------------------


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def upload_bytes(h: Any, data: bytes, *, filename: str = "records.pdf", kind: str = "records") -> tuple[str, str]:
    """Send `data` through the real upload routes and run its `finalize_upload` job inline, as the
    pipeline harness does (its own `upload` always sends kind `records`). Returns (upload id, document id)."""
    from tests.support.uploads import run_finalize

    client: httpx.AsyncClient = h.client
    body = {"kind": kind, "filename": filename, "size": len(data), "sha256": sha(data), "append": False}
    started = await client.post(f"/v1/cases/{h.case_id}/uploads", json=body)
    assert started.status_code == 201, started.text
    upload = started.json()["upload_id"]
    for n, at in enumerate(range(0, len(data), CHUNK), start=1):
        part = data[at : at + CHUNK]
        sent = await client.put(
            f"/v1/uploads/{upload}/chunks/{n}",
            content=part,
            headers={"content-type": "application/octet-stream", "x-chunk-sha256": sha(part)},
        )
        assert sent.status_code == 200, sent.text
    done = await client.post(f"/v1/uploads/{upload}/complete", json={"sha256": body["sha256"]})
    assert done.status_code == 202, done.text
    crypto: CaseCrypto = h.crypto
    await run_finalize(h.worker_dsn, h.admin, upload, h.storage, crypto, finish=True)
    status = await client.get(f"/v1/uploads/{upload}")
    assert status.status_code == 200, status.text
    document = status.json()["document_id"]
    assert document is not None, status.text
    return str(upload), str(document)
