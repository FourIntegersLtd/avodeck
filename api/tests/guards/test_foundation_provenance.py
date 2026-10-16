"""Rule 2: every fact has provenance (architecture 6.2.3).

`provenance.check(value, word_ids, words)` accepts a value only when it is found in the OCR words it
cites, after normalising case, spacing and number format, and only on whole-word boundaries.
`check_items` splits extracted items into accepted and rejected.
"""

from __future__ import annotations

import pytest

from app.core.ai import provenance
from app.core.ai.provenance import ProvenanceError

WORDS = {
    "w1": "Amoxicillin",
    "w2": "500mg",
    "w3": "TDS,",
    "w4": "1,000",
    "w5": "5.0",
    "w6": ".5",
    "w7": "120",
    "w8": "Seen",
    "w9": "by",
}


@pytest.mark.parametrize(
    ("value", "word_ids"),
    [
        ("amoxicillin", ["w1"]),
        ("AMOXICILLIN 500 mg", ["w1", "w2"]),
        ("Amoxicillin   500mg tds", ["w1", "w2", "w3"]),
        ("1000", ["w4"]),
        ("1,000", ["w4"]),
        ("5", ["w5"]),
        ("0.5", ["w6"]),
        ("500", ["w2"]),
        ("tds", ["w3"]),
    ],
)
def test_values_found_in_their_cited_words_are_accepted(value: str, word_ids: list[str]) -> None:
    provenance.check(value, word_ids, WORDS)
    assert provenance.supported(value, word_ids, WORDS)


@pytest.mark.parametrize(
    ("value", "word_ids", "reason"),
    [
        ("Penicillin", ["w1"], "not_in_words"),
        ("12", ["w7"], "not_in_words"),
        ("amoxi", ["w1"], "not_in_words"),
        ("Amoxicillin", ["w8", "w9"], "not_in_words"),
        ("Amoxicillin", [], "no_words"),
        ("Amoxicillin", ["w1", "w404"], "unknown_word"),
        ("", ["w1"], "empty_value"),
    ],
)
def test_values_not_in_their_cited_words_are_rejected(value: str, word_ids: list[str], reason: str) -> None:
    with pytest.raises(ProvenanceError) as caught:
        provenance.check(value, word_ids, WORDS)
    assert caught.value.reason == reason
    assert not provenance.supported(value, word_ids, WORDS)


def test_a_rejection_never_carries_the_value_or_the_words() -> None:
    with pytest.raises(ProvenanceError) as caught:
        provenance.check("Penicillin", ["w1"], WORDS)
    assert "Penicillin" not in str(caught.value) and "Amoxicillin" not in str(caught.value)


def test_check_items_splits_accepted_and_rejected_items() -> None:
    items = [
        {"kind": "prescription", "value": "Amoxicillin 500mg", "word_ids": ["w1", "w2"]},
        {"kind": "prescription", "value": "Penicillin", "word_ids": ["w1"]},
        {"kind": "result", "value": "120", "word_ids": []},
    ]
    accepted, rejected = provenance.check_items(items, WORDS)
    assert accepted == [items[0]]
    assert [(r.index, r.reason) for r in rejected] == [(1, "not_in_words"), (2, "no_words")]
