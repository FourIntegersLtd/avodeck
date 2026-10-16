"""Questions asked (architecture 4): slice C extracts and confirms them; D, E and F read them.

`text_verbatim` is the exact wording and never changes; an edited question keeps it and carries
`confirmed_text` (state-machines.md "Question").
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

QuestionStatus = Literal["suggested", "confirmed", "edited"]


class Question(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    case_id: uuid.UUID
    instruction_version_id: uuid.UUID | None = None
    number: int
    text_verbatim: str
    source_word_ids: list[str] = Field(default_factory=list)
    para_ref: str | None = None
    kind: str
    outside_expertise: bool = False
    status: QuestionStatus
    confirmed_text: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime
