"""The supplementary report's words (rule 8): only the changes, each with its reason, and the declaration.

The served version is never touched. The draft (built by the `build_supplementary` job) and the signed file
(built when the expert signs) come from the same blocks; signing adds the dated line.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from app.core.crypto import CaseCrypto
from app.core.packs import MergedPack
from app.features.after_serving import docs
from app.features.after_serving.common import (
    CHANGE_NEW,
    CHANGE_OLD,
    CHANGE_REASON,
    fetch_all,
    fetch_one,
    unseal,
)
from app.features.after_serving.words import long_date, word

_CITE = re.compile(r"\[\[cite:(\d{1,3})\]\]")


def plain_cites(text: str) -> str:
    return _CITE.sub(lambda m: f"[{m.group(1)}]", text)


async def blocks(
    conn: Any, crypto: CaseCrypto, case_id: uuid.UUID, pack: MergedPack, version_id: uuid.UUID
) -> list[docs.Block]:
    lang = pack.language
    version = await fetch_one(
        conn,
        "select v.prev_version_id, (select count(*) from app.report_versions o where o.case_id = v.case_id"
        " and o.type = 'supplementary' and o.version_no <= v.version_no) as n"
        " from app.report_versions v where v.id = %s and v.case_id = %s",
        (version_id, case_id),
    )
    assert version is not None
    served = await fetch_one(
        conn,
        "select v.display_name, (select min(s.served_on) from app.report_servings s where s.report_version_id = v.id)"
        " as served_on from app.report_versions v where v.id = %s",
        (version["prev_version_id"],),
    )
    out: list[docs.Block] = [("title", word(lang, "supplementary", n=int(version["n"])))]
    if served is not None and served["served_on"] is not None:
        out.append(
            (
                "body",
                word(
                    lang,
                    "supplementary_of",
                    name=served["display_name"],
                    date=long_date(lang, pack.date_order, served["served_on"]),
                ),
            )
        )
    rows = await fetch_all(
        conn,
        "select c.kind, c.old_text, c.new_text, c.reason, p.para_no from app.report_changes c"
        " join app.report_paragraphs p on p.id = c.paragraph_id"
        " where c.case_id = %s and c.supplementary_version_id = %s order by p.para_no, c.created_at",
        (case_id, version_id),
    )
    for r in rows:
        out.append(("heading", f"{word(lang, 'paragraph', n=r['para_no'])}: {word(lang, r['kind'])}"))
        out.append(("body", word(lang, "as_served")))
        out.append(("body", plain_cites(await unseal(conn, crypto, case_id, r["old_text"], CHANGE_OLD) or "")))
        out.append(("body", word(lang, "now_reads")))
        out.append(("body", plain_cites(await unseal(conn, crypto, case_id, r["new_text"], CHANGE_NEW) or "")))
        out.append(("body", word(lang, "reason")))
        out.append(("body", await unseal(conn, crypto, case_id, r["reason"], CHANGE_REASON) or ""))
    out.extend(docs.declaration_blocks(pack.declarations(), word(lang, "declaration")))
    return out
