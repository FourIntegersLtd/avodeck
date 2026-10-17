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


def extraction_purpose(page_id: uuid.UUID) -> str:
    """The purpose a page's sealed extraction (`page_analysis.extraction`) is sealed under."""
    return f"page_analysis/{page_id}/extraction"


def missing_description_purpose(item_id: object) -> str:
    """The purpose a missing item's description (`missing_items.description`) is sealed under."""
    return f"missing_items/{item_id}/description"


def missing_reason_purpose(item_id: object) -> str:
    """The purpose the reason a missing item is not available (`missing_items.reason`) is sealed under."""
    return f"missing_items/{item_id}/reason"
