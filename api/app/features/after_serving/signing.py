"""What slice G checks and freezes before it locks a supplementary report or a set of answers (PV-19a).

The main report's signing (slice F) refuses a cited fact that no longer stands, locks the draft the expert read, and
freezes what it was signed with. Slice G cannot import slice F, so the same protections are built here from core
contracts: `citable_facts` and `differs` (rule 2), `profile` (the named expert and the added declarations) and the
case's packs.

- `citations_stand`: every saved citation is still to a citable fact whose words and page match, on a page that is
  not set aside (a confirmed copy, quarantined or replaced) and is readable or typed. Ids only, never words.
- `draft_matches`: the stored draft's paragraphs are exactly the ones the sign would lock.
- `freeze`: the pack declarations and the expert's added wording, the expert, and the pack and guideline versions,
  sealed under the same purpose slice F uses, so `GET .../report/versions/{id}/snapshots` shows them.
"""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import Iterable, Sequence
from typing import Any

import docx

from app.core.contracts import profile
from app.core.contracts.citations import Citation, differs
from app.core.contracts.facts import citable_facts
from app.core.contracts.reading import PageReadLog
from app.core.crypto import CaseCrypto
from app.core.hashing import sha256_hex
from app.core.packs import MergedPack
from app.features.after_serving.common import fetch_all

# The pages a report may not stand on: a copy the expert confirmed, a quarantined or replaced page, or one the
# computer cannot read with nothing typed in its place (as slice F's `provenance.py`).
_UNUSABLE = (
    "select p.id from app.pages p left join app.page_analysis a on a.page_id = p.id"
    " where p.id = any(%s) and p.bundle_id = (select bundle_id from app.cases where id = %s) and ("
    "p.quarantined or p.superseded_by_page_id is not null"
    " or (p.duplicate_of_page_id is not null and coalesce(a.duplicate_status, '') = 'confirmed')"
    " or ((p.unreadable or a.read_failure is not null) and coalesce(length(p.transcribed_by_expert_text), 0) = 0))"
)


def snapshot_purpose(kind: str) -> str:
    """The purpose slice F seals and opens its snapshots under (so its snapshots route reads ours)."""
    return f"report_snapshot:{kind}"


async def citations_stand(
    conn: Any, case_id: uuid.UUID, reads: PageReadLog | None, saved: Iterable[Sequence[Any]]
) -> bool:
    """Whether every citation in `saved` (lists of citation JSON) still stands."""
    refs = [Citation.model_validate(c) for group in saved for c in group]
    if not refs:
        return True
    citable = {f.id: f for f in await citable_facts(conn, case_id, reads)}
    if any(differs(c, citable.get(c.fact_id)) for c in refs):
        return False
    unusable = await fetch_all(conn, _UNUSABLE, (sorted({c.page_id for c in refs}), case_id))
    return not unusable


async def draft_matches(
    conn: Any, storage: Any, crypto: CaseCrypto, case_id: uuid.UUID, path: str | None, blocks: Sequence[tuple[str, str]]
) -> bool:
    """Whether the stored draft says what `blocks` say: what the expert read is what is locked."""
    if path is None:
        return False
    data = await crypto.decrypt(conn, case_id, await storage.get(path), purpose=path)
    seen = [p.text for p in docx.Document(io.BytesIO(data)).paragraphs]
    return seen == [text for _, text in blocks]


def canonical(content: Any) -> bytes:
    return json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()


async def frozen(conn: Any, case_id: uuid.UUID, pack: MergedPack) -> dict[str, Any]:
    """What the version is signed with, by snapshot kind."""
    expert = await profile.named_expert(conn, case_id)
    return {
        "declarations": {
            "pack": [
                {"key": d["key"], "rule_ref": d.get("rule_ref"), "version": d.get("version"), "text": d["text"]}
                for d in pack.declarations()
            ],
            "added": [
                {"kind": d.kind, "text": d.text, "locked_at": d.locked_at}
                for d in await profile.declarations(conn, case_id)
            ],
        },
        "expert": {
            "person_id": expert.person_id if expert else None,
            "full_name": expert.full_name if expert else None,
            "registration_body": expert.registration_body if expert else None,
            "registration_number": expert.registration_number if expert else None,
        },
        "packs": pack.versions,
        "guidelines": pack.guidelines(),
    }


async def freeze(conn: Any, crypto: CaseCrypto, case_id: uuid.UUID, version_id: uuid.UUID, pack: MergedPack) -> None:
    """One sealed snapshot row per kind, while the version is still a draft (the database refuses it after)."""
    for kind, content in (await frozen(conn, case_id, pack)).items():
        plain = canonical(content)
        sealed = await crypto.encrypt(conn, case_id, plain, purpose=snapshot_purpose(kind))
        await conn.execute(
            "insert into app.report_snapshots (case_id, report_version_id, kind, content, sha256)"
            " values (%s, %s, %s, %s, %s)",
            (case_id, version_id, kind, sealed, sha256_hex(plain)),
        )
