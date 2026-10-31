"""Patient identifier recognisers for England and Wales (used by core/ai/pii.py)."""
from __future__ import annotations

import re

UK_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.I)
NHS_CANDIDATE = re.compile(r"\b(\d{3})[\s-]?(\d{3})[\s-]?(\d{4})\b")
# A date written after a date-of-birth label ("DOB: 12/03/1961", "Date of birth 12 March 1961").
_DATE = r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?,?\s+\d{2,4}"
DOB_LABELLED = re.compile(r"\b(?:D\.?\s?O\.?\s?B\.?|Date\s+of\s+birth|Born(?:\s+on)?)\s*[:\-]?\s*(" + _DATE + r")", re.I)
# UK phone numbers: +44 / 0044 / leading 0, then 9-10 digits in groups. (?<!\w) not \b so a leading "+" works.
UK_PHONE = re.compile(r"(?<![\w£$€,.])(?:(?:\+44|0044)\s*\(?0?\)?|0)\s*[1-9]\d{1,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}\b")
# Hospital, record, MRN and patient numbers: mask the value, keep the label.
HOSPITAL_NUMBER = re.compile(
    r"\b(?:Hospital\s+(?:No\.?|Number)|Record\s+(?:No\.?|Number)|MRN|Patient\s+(?:No\.?|Number|ID))\s*[:\-]?\s*([A-Z0-9][\w\-/]{2,20})\b",
    re.I,
)
NHS_LABEL = re.compile(r"\bNHS\s*(?:No\.?|Number|#)?", re.I)
_NHS_LABEL_REACH = 30


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
    """Return (start, end, kind) spans for NHS numbers, phones, hospital numbers, postcodes and labelled dates of birth."""
    spans = []
    label_ends = [m.end() for m in NHS_LABEL.finditer(text)]
    for m in NHS_CANDIDATE.finditer(text):
        # A bad check digit near an NHS label is still masked: one wrong OCR digit must not leak the number.
        near_label = any(0 <= m.start() - end <= _NHS_LABEL_REACH for end in label_ends)
        if near_label or valid_nhs_number("".join(m.groups())):
            spans.append((m.start(), m.end(), "NHS_NUMBER"))
    for m in UK_PHONE.finditer(text):
        spans.append((m.start(), m.end(), "PHONE_NUMBER"))
    for m in HOSPITAL_NUMBER.finditer(text):
        spans.append((m.start(1), m.end(1), "HOSPITAL_NUMBER"))
    for m in UK_POSTCODE.finditer(text):
        spans.append((m.start(), m.end(), "POSTCODE"))
    for m in DOB_LABELLED.finditer(text):
        spans.append((m.start(1), m.end(1), "DATE_OF_BIRTH"))
    return spans


def normalise_identifier(value: str) -> str | None:
    """The subject's NHS number as 10 digits (spaces and hyphens dropped), or None if it is not a valid one."""
    digits = re.sub(r"[\s-]", "", value)
    return digits if valid_nhs_number(digits) else None
