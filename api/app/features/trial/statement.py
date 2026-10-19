"""The joint statement to sign (screen 22): the meeting's items as agreed, then the pack's declarations
word for word and a place to sign."""

from __future__ import annotations

from typing import Any

from app.core.packs import MergedPack
from app.features.trial.pdfs import Document, render
from app.features.trial.words import word


def statement_pdf(
    pack: MergedPack, label: str, meeting: dict[str, Any], participants: list[str], items: list[dict[str, Any]]
) -> tuple[bytes, int]:
    lang = pack.language
    doc = Document(label)
    doc.para(word(lang, "discussion_on", date=meeting["discussion_on"].isoformat()))
    if participants:
        doc.para(word(lang, "participants", names=", ".join(participants)))
    for item in items:
        doc.heading(word(lang, "item", number=item["number"], text=item["text"]), 2)
        if item["own_position"]:
            doc.para(word(lang, "own_view", text=item["own_position"]))
        if item["other_positions"]:
            doc.para(word(lang, "other_views", text=item["other_positions"]))
        if item["agreed"] is True:
            doc.para(word(lang, "agreed"))
        elif item["agreed"] is False:
            doc.para(word(lang, "not_agreed", text=item["reason"] or ""))
    doc.heading(word(lang, "declarations"), 2)
    doc.paras(str(line) for line in meeting["declarations"])
    for _ in participants or [""]:
        doc.para(word(lang, "signed_by"))
    return render(doc, body=14)
