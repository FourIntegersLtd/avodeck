"""Where a page's files live in Storage. Each file is sealed with its own path as the purpose."""

from __future__ import annotations

import uuid


def page_prefix(case_id: uuid.UUID, page_id: uuid.UUID) -> str:
    return f"cases/{case_id}/pages/{page_id}"


def page_pdf_path(case_id: uuid.UUID, page_id: uuid.UUID) -> str:
    return f"{page_prefix(case_id, page_id)}/page.pdf"


def page_image_path(case_id: uuid.UUID, page_id: uuid.UUID) -> str:
    return f"{page_prefix(case_id, page_id)}/image.jpg"


def thumbnail_path(case_id: uuid.UUID, page_id: uuid.UUID) -> str:
    return f"{page_prefix(case_id, page_id)}/thumbnail.jpg"
