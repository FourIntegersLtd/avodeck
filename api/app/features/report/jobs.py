"""Slice F's worker jobs: `build_report` (screen 16) and `final_check` (screen 17).

`build_report` assembles the case's report (`assemble.py`), lays it out in the named expert's own Word
template (`docx_out.py`, falling back to the standard layout, F16) and as a PDF, and stores a new draft
`report_versions` row with both files sealed under the case key, one `report_paragraphs` row per
paragraph (text sealed), one `citations` row per rendered citation (value and quote sealed) and a
`report_builds` row (citation style, template, fallback, page count, pack versions). Older drafts and
their files are kept. It runs once per job (a retried job does not build twice). No text reaches a log
or a job row (rule 6).

`final_check` runs the core and pack checks (`checks.py`) on the case's latest draft and T-17 over the
expert's own paragraphs, and stores one `final_check_runs` row: results as keys, outcomes and "Fix it"
targets, warnings as places in the draft (never words), whether it passed and the pack versions.
"""

from __future__ import annotations

import base64
import logging
import uuid

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.core.contracts import profile
from app.core.contracts.reports import AFTER_SERVING_TYPES
from app.core.crypto import CaseCrypto, SecretBox
from app.core.jobs import JobContext, registry
from app.core.storage import SupabaseStorage, case_prefix
from app.features.report.assemble import (
    PARAGRAPH_PURPOSE,
    QUOTE_PURPOSE,
    VALUE_PURPOSE,
    Assembly,
    assemble,
    template_mapping,
)
from app.features.report.checks import gather, run_checks, tone_warnings
from app.features.report.common import pack_for
from app.features.report.docx_out import render_docx
from app.features.report.pdf_out import render_pdf
from app.features.report.services import current
from app.features.report.words import word

logger = logging.getLogger("app.report")

TEMPLATE_PURPOSE = "report_template"


def file_path(case_id: uuid.UUID, version_id: uuid.UUID, name: str) -> str:
    return f"{case_prefix(case_id)}/report/{version_id}/{name}"


async def _template_file(
    storage: SupabaseStorage, crypto: CaseCrypto, template: profile.ReportTemplate
) -> bytes | None:
    """The template as slice C sealed it (cell box, subject owner:template); None if it cannot be opened."""
    try:
        owner = template.storage_path.split("/")[1]
        sealed = await storage.get(template.storage_path)
        box = SecretBox(crypto.provider)
        opened = box.open(sealed.decode(), purpose=TEMPLATE_PURPOSE, subject=f"{owner}:{template.id}")
        return base64.b64decode(opened)
    except Exception as exc:  # noqa: BLE001 - an unusable template falls back (F16)
        logger.warning("report template unavailable: %s", type(exc).__name__)
        return None


async def _store_rows(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, version_id: uuid.UUID, assembly: Assembly
) -> None:
    para_no = 0
    for block in assembly.blocks:
        for para in block.paragraphs:
            para_no += 1
            sealed = await crypto.encrypt(conn, case_id, para.text.encode(), purpose=PARAGRAPH_PURPOSE)
            cur = await conn.execute(
                "insert into app.report_paragraphs (case_id, report_version_id, para_no, section,"
                " content_control_id, text, question_id) values (%s, %s, %s, %s, %s, %s, %s) returning id",
                (case_id, version_id, para_no, block.key, f"section:{block.key}", sealed, para.question_id),
            )
            row = await cur.fetchone()
            assert row is not None
            for cited in para.cites:
                value = await crypto.encrypt(conn, case_id, (cited.fact.value or "").encode(), purpose=VALUE_PURPOSE)
                quote = await crypto.encrypt(conn, case_id, cited.quote.encode(), purpose=QUOTE_PURPOSE)
                await conn.execute(
                    "insert into app.citations (case_id, report_version_id, paragraph_id, cite_no, fact_id,"
                    " fact_version, value_snapshot, quote_verbatim, page_id)"
                    " values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        case_id,
                        version_id,
                        row[0],
                        cited.cite_no,
                        cited.fact.id,
                        cited.fact.version,
                        value,
                        quote,
                        cited.fact.page_id,
                    ),
                )


async def build(conn: AsyncConnection, case_id: uuid.UUID, job_id: uuid.UUID | None) -> uuid.UUID:
    """Build a new draft of the case's report on the job's connection; returns its version id."""
    services = current()
    crypto, storage = services.case_crypto(), services.files()
    pack = await pack_for(conn, services.packs, case_id)
    cur = await conn.execute("select citation_style from app.report_settings where case_id = %s", (case_id,))
    row = await cur.fetchone()
    style = str(row[0]) if row else "page"
    template = await profile.report_template(conn, case_id)
    data = await _template_file(storage, crypto, template) if template is not None else None
    assembly = await assemble(conn, case_id, pack, services.reads, crypto, template, style)
    docx_bytes, fallback = render_docx(assembly, data, template)
    headings: dict[str, str] = {}
    if template is not None and not fallback:
        texts = {int(h["index"]): str(h.get("text", "")) for h in template.heading_map if isinstance(h, dict)}
        mapped = template_mapping([b.key for b in assembly.blocks], template)
        headings = {k: texts[i] for k, i in mapped.items() if i in texts}
    pdf_bytes, pages = render_pdf(assembly, headings)

    cur = await conn.execute(
        "select coalesce(max(version_no), 0) + 1 from app.report_versions where case_id = %s", (case_id,)
    )
    numbered = await cur.fetchone()
    assert numbered is not None
    version_no = int(numbered[0])
    version_id = uuid.uuid4()
    docx_path = file_path(case_id, version_id, "report.docx")
    pdf_path = file_path(case_id, version_id, "report.pdf")
    await storage.put(docx_path, await crypto.encrypt(conn, case_id, docx_bytes, purpose=docx_path))
    await storage.put(pdf_path, await crypto.encrypt(conn, case_id, pdf_bytes, purpose=pdf_path))
    await conn.execute(
        "insert into app.report_versions (id, case_id, version_no, type, display_name, docx_path, pdf_path)"
        " values (%s, %s, %s, 'main', %s, %s, %s)",
        (version_id, case_id, version_no, word(pack.language, "draft", n=version_no), docx_path, pdf_path),
    )
    await _store_rows(conn, crypto, case_id, version_id, assembly)
    await conn.execute(
        "insert into app.report_builds (report_version_id, case_id, job_id, citation_style, template_id,"
        " template_fallback, page_count, pack_versions) values (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            version_id,
            case_id,
            job_id,
            style,
            template.id if template is not None else None,
            fallback,
            pages,
            Jsonb(pack.versions),
        ),
    )
    return version_id


@registry.kind("build_report", interactive=True, changes_case=False)
async def build_report(ctx: JobContext) -> None:
    async def work(conn: AsyncConnection) -> None:
        await build(conn, ctx.case_id, ctx.job_id)

    await ctx.step("build", work)


async def latest_draft(conn: AsyncConnection, case_id: uuid.UUID) -> uuid.UUID | None:
    """The newest draft of this slice's report; slice G's own versions (answers, supplementary) are skipped."""
    cur = await conn.execute(
        "select id from app.report_versions where case_id = %s and status = 'draft' and type <> all(%s)"
        " order by version_no desc limit 1",
        (case_id, list(AFTER_SERVING_TYPES)),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def check(conn: AsyncConnection, case_id: uuid.UUID, job_id: uuid.UUID | None) -> uuid.UUID | None:
    """Run the final check on the latest draft on the job's connection; returns the run's id."""
    services = current()
    version_id = await latest_draft(conn, case_id)
    if version_id is None:
        return None
    pack = await pack_for(conn, services.packs, case_id)
    inputs = await gather(conn, case_id, pack, services.case_crypto(), services.reads, version_id)
    results = await run_checks(inputs)
    warnings, status = await tone_warnings(inputs, services.kit)
    cur = await conn.execute(
        "insert into app.final_check_runs (case_id, report_version_id, results, warnings, warnings_status, passed,"
        " pack_versions, job_id) values (%s, %s, %s, %s, %s, %s, %s, %s) returning id",
        (
            case_id,
            version_id,
            Jsonb([r.out() for r in results]),
            Jsonb(warnings),
            status,
            all(r.outcome != "failed" for r in results),
            Jsonb(pack.versions),
            job_id,
        ),
    )
    row = await cur.fetchone()
    return row[0] if row else None


@registry.kind("final_check", interactive=True, changes_case=False)
async def final_check(ctx: JobContext) -> None:
    async def work(conn: AsyncConnection) -> None:
        await check(conn, ctx.case_id, ctx.job_id)

    await ctx.step("check", work)
