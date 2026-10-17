"""Patient identifier recognisers for England and Wales (used by core/ai/pii.py)."""
import re

UK_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.I)
NHS_CANDIDATE = re.compile(r"\b(\d{3})[\s-]?(\d{3})[\s-]?(\d{4})\b")
# A date written after a date-of-birth label ("DOB: 12/03/1961", "Date of birth 12 March 1961").
_DATE = r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?,?\s+\d{2,4}"
DOB_LABELLED = re.compile(r"\b(?:D\.?\s?O\.?\s?B\.?|Date\s+of\s+birth|Born(?:\s+on)?)\s*[:\-]?\s*(" + _DATE + r")", re.I)


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
    """Return (start, end, kind) spans for NHS numbers, postcodes and labelled dates of birth."""
    spans = []
    for m in NHS_CANDIDATE.finditer(text):
        if valid_nhs_number("".join(m.groups())):
            spans.append((m.start(), m.end(), "NHS_NUMBER"))
    for m in UK_POSTCODE.finditer(text):
        spans.append((m.start(), m.end(), "POSTCODE"))
    for m in DOB_LABELLED.finditer(text):
        spans.append((m.start(1), m.end(1), "DATE_OF_BIRTH"))
    return spans
