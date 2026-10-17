"""Rule 2: signs and comparators are part of a value, and cited words form one run (architecture 6.2.3).

"<60" is not "60", "-2.5" is not "2.5" and "45%" is not "45": a sign or comparator in front of a
number (not after a letter or digit, so "1-2" and "Co-amoxiclav" keep their hyphen as a gap) and a
percent sign after it stay with the number. The cited words must be one unbroken run in reading
order (the order of the page's words), so a value cannot be stitched from words far apart.
"""

from __future__ import annotations

import pytest

from app.core.ai import provenance
from app.core.ai.provenance import ProvenanceError

WORDS = {
    "s1": "eGFR",
    "s2": "<60",
    "s3": "-2.5",
    "s4": "+3",
    "s5": "≥",
    "s6": "90",
    "s7": "45%",
    "s8": "1-2",
    "s9": "Co-amoxiclav",
}


@pytest.mark.parametrize(
    ("value", "word_ids"),
    [
        ("<60", ["s2"]),
        ("< 60", ["s2"]),
        ("eGFR <60", ["s1", "s2"]),
        ("-2.5", ["s3"]),
        ("−2.5", ["s3"]),
        ("+3", ["s4"]),
        ("≥ 90", ["s5", "s6"]),
        (">=90", ["s5", "s6"]),
        ("45%", ["s7"]),
        ("45 %", ["s7"]),
        ("1-2", ["s8"]),
        ("1 2", ["s8"]),
        ("co-amoxiclav", ["s9"]),
    ],
)
def test_values_keep_their_signs_and_are_accepted(value: str, word_ids: list[str]) -> None:
    provenance.check(value, word_ids, WORDS)


@pytest.mark.parametrize(
    ("value", "word_ids"),
    [
        ("60", ["s2"]),
        (">60", ["s2"]),
        ("2.5", ["s3"]),
        ("+2.5", ["s3"]),
        ("3", ["s4"]),
        ("90", ["s5", "s6"]),
        ("≤ 90", ["s5", "s6"]),
        ("45", ["s7"]),
    ],
)
def test_dropping_or_changing_a_sign_is_refused(value: str, word_ids: list[str]) -> None:
    with pytest.raises(ProvenanceError) as caught:
        provenance.check(value, word_ids, WORDS)
    assert caught.value.reason == "not_in_words"


@pytest.mark.parametrize(
    ("value", "word_ids"),
    [
        ("eGFR 45%", ["s1", "s7"]),
        ("eGFR", ["s1", "s3"]),
        ("<60 90", ["s2", "s6"]),
    ],
)
def test_cited_words_must_be_one_unbroken_run(value: str, word_ids: list[str]) -> None:
    with pytest.raises(ProvenanceError) as caught:
        provenance.check(value, word_ids, WORDS)
    assert caught.value.reason == "not_contiguous"


def test_cited_words_are_read_in_page_order_whatever_order_they_are_cited_in() -> None:
    provenance.check("eGFR <60", ["s2", "s1"], WORDS)
    with pytest.raises(ProvenanceError) as caught:
        provenance.check("<60 eGFR", ["s2", "s1"], WORDS)
    assert caught.value.reason == "not_in_words"
