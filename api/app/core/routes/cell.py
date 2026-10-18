"""GET /v1/cell: what the sign-in screen shows about this cell (support line, where files are kept).

Public: read before anyone is signed in. The values come from the cell's environment; a value the cell
does not set is null. Nothing here is about a person or a case.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.core.config import cell_public
from app.core.http import public_router

cell_public_router = public_router(prefix="/v1", tags=["cell"])


class Cell(BaseModel):
    support_phone: str | None
    support_hours: str | None
    storage_place: str | None


@cell_public_router.get("/cell", response_model=Cell)
async def get_cell() -> Cell:
    c = cell_public()
    return Cell(support_phone=c.support_phone, support_hours=c.support_hours, storage_place=c.storage_place)
