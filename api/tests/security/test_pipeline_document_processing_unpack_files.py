"""Audit 13, reviewer P3: the unpack call neither takes nor returns a whole file through the pipe.

A zip (up to the upload limit) used to be read whole in the worker and pickled to the child, and its members were
pickled back, so a big one could pass the child's memory limit and be marked damaged for it. The child now gets the
scratch file's path and writes what it finds (the members, the converted PDF) to scratch files, and the worker reads
them one at a time. The members still arrive with the right size and hash. The sandbox is wrapped to measure the
calls; the contents are random bytes, not patient text.
"""

from __future__ import annotations

import hashlib
import io
import os
import pickle
import zipfile
from typing import Any

import pikepdf
import pytest

from app.core import sandbox
from tests.repository.test_pipeline_support import Harness
from tests.security import test_foundation_file_lifecycle_validation as lifecycle
from tests.support.file_lifecycle_documents import _save, _typed_page

pytestmark = pytest.mark.anyio

h = lifecycle.h
storage = lifecycle.storage

SMALL = 64 * 1024


def a_pdf() -> bytes:
    doc = pikepdf.new()
    _typed_page(doc, "keep one")
    return _save(doc)


def measure(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int, int]]:
    """(function name, largest bytes argument, pickled size of the result) of every unpack call."""
    real = sandbox.run
    seen: list[tuple[str, int, int]] = []

    async def wrapped(fn: Any, *args: Any, timeout: float | None = None) -> Any:
        out = await real(fn, *args, timeout=timeout)
        if fn.__name__.startswith("unpack"):
            biggest = max((len(a) for a in args if isinstance(a, bytes | bytearray)), default=0)
            seen.append((fn.__name__, biggest, len(pickle.dumps(out))))
        return out

    monkeypatch.setattr(sandbox, "run", wrapped)
    return seen


async def test_a_zip_is_unpacked_from_its_path_and_its_members_come_back_as_files(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob = os.urandom(300 * 1024)  # incompressible, so the zip itself is big too
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        z.writestr("a-letter.pdf", a_pdf())
        z.writestr("scan.bin", blob)
    seen = measure(monkeypatch)
    await lifecycle.run(h, out.getvalue(), "batch.zip")

    assert seen, "the unpack call was not measured"
    for name, biggest, answer in seen:
        assert biggest <= SMALL, f"{name} was sent a whole file ({biggest} bytes)"
        assert answer <= SMALL, f"{name} sent a whole file back ({answer} bytes)"
    row = h.admin.execute(
        "select p.size, d.sha256 from app.document_parts p join app.documents d on d.id = p.document_id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s) and p.part_no = 2",
        (h.case_id,),
    ).fetchone()
    assert row is not None and int(row[0]) == len(blob) and row[1] == hashlib.sha256(blob).hexdigest()
