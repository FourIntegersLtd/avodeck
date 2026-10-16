"""Fixture identifiers: ZZ-ID-123456 (6 digits) and a date after "Birthdate:" (month first)."""
import re

ZZ_ID = re.compile(r"\bZZ-ID-\d{6}\b")
BIRTHDATE = re.compile(r"\bBirthdate:\s*(\d{1,2}/\d{1,2}/\d{4})")


def find_identifiers(text: str) -> list[tuple[int, int, str]]:
    spans = [(m.start(), m.end(), "ZZ_ID") for m in ZZ_ID.finditer(text)]
    spans += [(m.start(1), m.end(1), "DATE_OF_BIRTH") for m in BIRTHDATE.finditer(text)]
    return spans
