"""Rule 3 for the records transcript: the entries route is outside the blind-read gate, so it must never
carry a fact, a suggestion, a value or a status. Its response model may have only these fields."""

from __future__ import annotations

from app.features.reader.schemas import TranscriptEntryOut, TranscriptList

ALLOWED = {
    "id", "page_no", "kind", "kind_label", "folded", "author_role", "author_label", "specialty", "event_date",
    "date_basis", "lines", "results", "result_labels",
}  # fmt: skip
FORBIDDEN = ("fact", "value", "suggest", "status_of", "word_ids", "verified", "flag")


def test_an_entry_carries_only_what_is_printed_on_the_page() -> None:
    fields = set(TranscriptEntryOut.model_fields)
    assert fields == ALLOWED
    assert not [f for f in fields if any(bad in f for bad in FORBIDDEN)]


def test_the_list_carries_entries_and_nothing_about_facts() -> None:
    assert set(TranscriptList.model_fields) == {"status", "total", "entries", "picture_pages", "unread_pages"}
