"""Fixture identifier: ZZ-ID-123456 (6 digits)."""
import re

ZZ_ID = re.compile(r"\bZZ-ID-\d{6}\b")


def find_identifiers(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), "ZZ_ID") for m in ZZ_ID.finditer(text)]
