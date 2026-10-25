"""GET /v1/cell: what the sign-in screen shows about this cell (where files are kept).

Public: read before anyone is signed in. The values come from the cell's environment; a value the cell
does not set is null. Nothing here is about a person or a case.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.core.config import cell_public, support_email
from app.core.http import public_router

cell_public_router = public_router(prefix="/v1", tags=["cell"])


class Cell(BaseModel):
    storage_place: str | None
    help_form: bool  # a Help form exists; the address it goes to is never sent


@cell_public_router.get("/cell", response_model=Cell)
async def get_cell() -> Cell:
    return Cell(storage_place=cell_public().storage_place, help_form=bool(support_email()))
