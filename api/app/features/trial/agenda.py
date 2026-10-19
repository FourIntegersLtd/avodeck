"""Reading an agenda (T-22a) and filling each item from the served report (T-22b).

T-22a copies each numbered item's words with their ids; an item whose words are not exactly those words
(after normalising case and spacing) is dropped (rule 2). T-22b returns paragraph references only; code
puts the served paragraph in word for word. A new agenda version keeps the expert's work on items that are
the same (by number) and says which items changed.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from psycopg import AsyncConnection

from app.core.ai.kit import AiKit
from app.core.contracts.served import ServedReport
from app.core.crypto import CaseCrypto
from app.core.packs import MergedPack
from app.features.trial import ai_tasks
from app.features.trial.common import AGENDA_TEXT, AGREED_REASON, CHANGE_NOTE, OTHER_POSITIONS, POSITION, seal

_SPACE = re.compile(r"\s+")


def same_words(a: str, b: str) -> bool:
    return _SPACE.sub(" ", a).strip().casefold() == _SPACE.sub(" ", b).strip().casefold()


def copied_items(answer: dict[str, Any], words: dict[str, str]) -> list[tuple[int, str, list[str]]]:
    """(number, text, word ids) of each item whose words are exactly the cited words, one per number."""
    out: dict[int, tuple[int, str, list[str]]] = {}
    for item in answer.get("items", []):
        ids = list(item["word_ids"])
        if not ids or any(w not in words for w in ids) or len(set(ids)) != len(ids):
            continue
        text = " ".join(words[w] for w in ids)
        if same_words(text, item["text_verbatim"]) and item["number"] not in out:
            out[item["number"]] = (item["number"], text, ids)
    return [out[n] for n in sorted(out)]


def served_text(served: ServedReport | None, paragraph_ids: list[uuid.UUID]) -> str | None:
    if served is None or not paragraph_ids:
        return None
    by_id = {p.id: p.text for p in served.paragraphs}
    texts = [by_id[p] for p in paragraph_ids if p in by_id]
    return "\n\n".join(texts) if texts else None


async def links(
    conn: AsyncConnection,
    kit: AiKit,
    pack: MergedPack,
    case_id: uuid.UUID,
    texts: list[str],
    served: ServedReport,
) -> list[list[uuid.UUID]]:
    """The served paragraphs (ids) dealing with each agenda item (T-22b)."""
    agenda_refs = [f"a{i}" for i in range(1, len(texts) + 1)]
    by_ref = {f"p{p.para_no}": p for p in served.paragraphs}
    request = "\n".join(
        [*(f"{ref}\t{t}" for ref, t in zip(agenda_refs, texts, strict=True))]
        + [f"{ref}\t{p.text}" for ref, p in by_ref.items()]
    )
    task = ai_tasks.t22b(pack, agenda_refs, list(by_ref))
    answer = await kit.run(conn, task, pack=pack, text=request, case_id=case_id, subject="agenda_links")
    out: list[list[uuid.UUID]] = [[] for _ in texts]
    for link in answer.get("links", []):
        index = agenda_refs.index(link["agenda"])
        for ref in link["paragraphs"]:
            if by_ref[ref].id not in out[index]:
                out[index].append(by_ref[ref].id)
    return out


async def insert_items(
    conn: AsyncConnection,
    crypto: CaseCrypto,
    kit: AiKit | None,
    pack: MergedPack,
    case_id: uuid.UUID,
    meeting_id: uuid.UUID,
    version_id: uuid.UUID,
    copied: list[tuple[int, str, list[str]]],
    previous: list[dict[str, Any]] | None,
    served: ServedReport | None,
) -> None:
    before = {i["number"]: i for i in previous or []}
    fresh = [c for c in copied if c[0] not in before]
    linked: dict[int, list[uuid.UUID]] = {}
    if fresh and served is not None and served.paragraphs and kit is not None:
        found = await links(conn, kit, pack, case_id, [c[1] for c in fresh], served)
        linked = {c[0]: ids for c, ids in zip(fresh, found, strict=True)}
    for number, text, word_ids in copied:
        old = before.get(number)
        if old is not None:
            same = same_words(old["text"], text)
            values = {
                "served": old["served_paragraph_ids"],
                "own": old["own_position"],
                "others": old["other_positions"],
                "agreed": old["agreed"] if same else None,
                "reason": old["reason"] if same else None,
                "note": old["change_note"],
                "deviation": old["deviation"],
                "deviation_reason": old["deviation_reason"],
                "changed": "same" if same else "changed",
                "recorded": old["change_recorded_at"],
            }
        else:
            served_ids = linked.get(number, [])
            own = served_text(served, served_ids)
            values = {
                "served": served_ids,
                "own": own,
                "others": None,
                "agreed": None,
                "reason": None,
                "note": None,
                "deviation": "consistent" if own is not None else None,
                "deviation_reason": "copied_from_report" if own is not None else None,
                "changed": "new" if previous is not None else None,
                "recorded": None,
            }
        await conn.execute(
            "insert into app.agenda_items (case_id, meeting_id, agenda_version_id, number, text, word_ids,"
            " served_paragraph_ids, own_position, other_positions, agreed, reason, change_note, deviation,"
            " deviation_reason, changed_from_previous, change_recorded_at)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                case_id,
                meeting_id,
                version_id,
                number,
                await seal(conn, crypto, case_id, text, AGENDA_TEXT),
                word_ids,
                values["served"],
                await seal(conn, crypto, case_id, values["own"], POSITION),
                await seal(conn, crypto, case_id, values["others"], OTHER_POSITIONS),
                values["agreed"],
                await seal(conn, crypto, case_id, values["reason"], AGREED_REASON),
                await seal(conn, crypto, case_id, values["note"], CHANGE_NOTE),
                values["deviation"],
                values["deviation_reason"],
                values["changed"],
                values["recorded"],
            ),
        )
