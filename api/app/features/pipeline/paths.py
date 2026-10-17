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


def word_purpose(page_id: uuid.UUID, word_id: str) -> str:
    """The purpose each word in `ocr_words` is sealed under, so a word cannot be moved to another row."""
    return f"ocr_words/{page_id}/{word_id}"
