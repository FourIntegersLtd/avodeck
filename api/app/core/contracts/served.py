"""The case's latest served report, paragraph by paragraph (slice F writes it; slices G and I read it here).

Paragraph text is sealed by slice F under `PARAGRAPH_PURPOSE` and is opened here: it is the expert's served
words and may hold patient details, so never log it (rule 6) and strip identifiers before any model call
(rule 7). Each paragraph lists the pages it cites as (page id, bundle page number). Runs on the caller's
connection, so row-level security applies; `None` when nothing has been served.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from psycopg import AsyncConnection

from app.core.crypto import CaseCrypto

PARAGRAPH_PURPOSE = "report_paragraph"


@dataclass(frozen=True)
class ServedParagraph:
    id: uuid.UUID
    para_no: int
    section: str
    text: str
    cited: tuple[tuple[uuid.UUID, int], ...]


@dataclass(frozen=True)
class ServedReport:
    version_id: uuid.UUID
    version_no: int
    served_on: date | None
    paragraphs: tuple[ServedParagraph, ...]


async def latest_served(conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID) -> ServedReport | None:
    cur = await conn.execute(
        "select v.id, v.version_no, (select max(s.served_on) from app.report_servings s"
        " where s.report_version_id = v.id) from app.report_versions v"
        " where v.case_id = %s and v.status = 'served' order by v.version_no desc limit 1",
        (case_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    version_id, version_no, served_on = row
    cur = await conn.execute(
        "select c.paragraph_id, c.page_id, p.bundle_page_no from app.citations c"
        " join app.pages p on p.id = c.page_id where c.report_version_id = %s order by c.paragraph_id, c.cite_no",
        (version_id,),
    )
    cited: dict[uuid.UUID, list[tuple[uuid.UUID, int]]] = {}
    for paragraph_id, page_id, page_no in await cur.fetchall():
        pages = cited.setdefault(paragraph_id, [])
        if (page_id, page_no) not in pages:
            pages.append((page_id, int(page_no)))
    cur = await conn.execute(
        "select id, para_no, section, text from app.report_paragraphs where report_version_id = %s order by para_no",
        (version_id,),
    )
    paragraphs = []
    for pid, para_no, section, sealed in await cur.fetchall():
        text = await crypto.decrypt(conn, case_id, bytes(sealed), purpose=PARAGRAPH_PURPOSE)
        paragraphs.append(ServedParagraph(pid, int(para_no), str(section), text.decode(), tuple(cited.get(pid, []))))
    return ServedReport(version_id, int(version_no), served_on, tuple(paragraphs))
