"""The certificate of deletion as a PDF (screen 26; F29): the same counts, hashes and dates as the
JSON certificate, never a case name, a person's name or patient text (rule 6). The words come from
`words/<language>.json`; values are shown as stored (codes, numbers, hashes, dates).
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from app.core.pdf_document import Section, render_record
from app.features.close.schemas import Certificate

_WORDS = Path(__file__).with_name("words")


@cache
def words(language: str = "en-GB") -> dict[str, str]:
    data: dict[str, str] = json.loads((_WORDS / f"{language}.json").read_text(encoding="utf-8"))
    return data


def _value(value: Any) -> str:
    if isinstance(value, list | tuple):
        return ", ".join(_value(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_value(v)}" for k, v in sorted(value.items()))
    return str(value)


def _rows(data: dict[str, Any], none: str) -> list[tuple[str, str]]:
    return [(str(k), _value(v)) for k, v in sorted(data.items())] or [(none, "")]


def certificate_pdf(cert: Certificate, language: str = "en-GB") -> bytes:
    w = words(language)
    expiry = cert.backup_expiry_on.isoformat() if cert.backup_expiry_on else w["close.certificate.backup_expiry_none"]
    return render_record(
        w["close.certificate.title"],
        [
            Section(
                None,
                [
                    (w["close.certificate.case_reference"], str(cert.case_id)),
                    (w["close.certificate.deleted_at"], cert.deleted_at.isoformat(timespec="seconds")),
                    (w["close.certificate.backup_expiry_on"], expiry),
                    (w["close.certificate.sent_to"], _value(cert.sent_to) or w["close.certificate.none"]),
                ],
            ),  # fmt: skip
            Section(w["close.certificate.counts"], _rows(cert.counts, w["close.certificate.none"])),
            Section(w["close.certificate.hashes"], _rows(cert.hashes, w["close.certificate.none"])),
            Section(w["close.certificate.log_chain_heads"], _rows(cert.log_chain_heads, w["close.certificate.none"])),
        ],
    )
