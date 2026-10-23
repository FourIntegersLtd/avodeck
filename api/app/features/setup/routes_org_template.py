"""Screen 04: the report template an organisation requires for its cases (architecture 10.1).

A case has no template choice of its own: its report is built into the template of the person it is named
to (or, when the organisation requires one, the organisation's template; slice F builds with it). So a
required template is chosen automatically and is never offered as a choice, and nothing is refused here.
The New case screen asks which template a case for an organisation will use and shows it locked.
Only a current member can ask; anyone else gets 404.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.contracts.org_policy import org_policy
from app.core.http import DbConn
from app.features.setup.router import router


class NewCaseTemplate(BaseModel):
    organisation_id: uuid.UUID
    required_template_id: uuid.UUID | None = Field(description="The organisation's required report template, if any.")
    locked: bool = Field(description="True when the organisation requires a template: used automatically, no choice.")


@router.get("/new-case/template", response_model=NewCaseTemplate)
async def new_case_template(organisation_id: uuid.UUID, conn: DbConn) -> NewCaseTemplate:
    policy = await org_policy(conn, organisation_id)
    return NewCaseTemplate(
        organisation_id=organisation_id,
        required_template_id=policy.required_template_id,
        locked=policy.required_template_id is not None,
    )
