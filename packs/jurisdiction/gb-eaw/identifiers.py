"""Patient identifier recognisers for England and Wales (used by core/ai/pii.py)."""
import re

UK_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.I)
NHS_CANDIDATE = re.compile(r"\b(\d{3})[\s-]?(\d{3})[\s-]?(\d{4})\b")


def valid_nhs_number(digits: str) -> bool:
    """NHS number check digit (modulus 11)."""
    if len(digits) != 10 or not digits.isdigit():
        return False
    total = sum(int(d) * w for d, w in zip(digits[:9], range(10, 1, -1)))
    check = 11 - (total % 11)
    if check == 11:
        check = 0
    return check != 10 and check == int(digits[9])


def find_identifiers(text: str) -> list[tuple[int, int, str]]:
    """Return (start, end, kind) spans for NHS numbers and postcodes."""
    spans = []
    for m in NHS_CANDIDATE.finditer(text):
        if valid_nhs_number("".join(m.groups())):
            spans.append((m.start(), m.end(), "NHS_NUMBER"))
    for m in UK_POSTCODE.finditer(text):
        spans.append((m.start(), m.end(), "POSTCODE"))
    return spans
