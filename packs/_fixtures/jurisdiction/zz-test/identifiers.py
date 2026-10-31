"""Fixture identifiers: ZZ-ID-123456 (6 digits) and a date after "Birthdate:" (month first)."""
from __future__ import annotations

import re

ZZ_ID = re.compile(r"\bZZ-ID-\d{6}\b")
ZZ_PHONE = re.compile(r"\b555-\d{4}\b")
ZZ_HOSPITAL = re.compile(r"\bZZ-HOSP-\d{4}\b")
BIRTHDATE = re.compile(r"\bBirthdate:\s*(\d{1,2}/\d{1,2}/\d{4})")


def find_identifiers(text: str) -> list[tuple[int, int, str]]:
    spans = [(m.start(), m.end(), "ZZ_ID") for m in ZZ_ID.finditer(text)]
    spans += [(m.start(), m.end(), "ZZ_PHONE") for m in ZZ_PHONE.finditer(text)]
    spans += [(m.start(), m.end(), "ZZ_HOSPITAL") for m in ZZ_HOSPITAL.finditer(text)]
    spans += [(m.start(1), m.end(1), "DATE_OF_BIRTH") for m in BIRTHDATE.finditer(text)]
    return spans


def normalise_identifier(value: str) -> str | None:
    """ZZ-ID-123456 in capitals, or None."""
    text = value.strip().upper()
    return text if re.fullmatch(r"ZZ-ID-\d{6}", text) else None
