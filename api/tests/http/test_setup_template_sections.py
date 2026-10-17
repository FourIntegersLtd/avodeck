"""Screen 02: the template's heading map offers the report sections the packs know (rule 11).

The expert has no case yet when mapping their template, so the template itself says which section keys a
heading can be mapped to: every pack combination's `report_sections` in this cell, plus `other`, which
always comes last. These are the only keys `PATCH /v1/me/template/mapping` accepts.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.packs import default_library
from tests.http.test_setup_template_suggestions import (  # noqa: F401  (pytest fixtures)
    app,
    expert,
    storage,
    upload_template,
)
from tests.support.db import ClientFor
from tests.support.packs import LAUNCH

pytestmark = pytest.mark.anyio


def launch_sections() -> list[str]:
    merged = default_library().combine(*LAUNCH.codes)
    return [s if isinstance(s, str) else str(s["key"]) for s in merged.report_sections()]


async def test_the_template_lists_the_sections_a_heading_can_be_mapped_to(
    client_for: ClientFor,
    expert: uuid.UUID,  # noqa: F811
) -> None:
    client = await client_for(expert)
    up = await upload_template(client)
    expected = [*launch_sections(), "other"]
    assert up.json()["sections"] == expected
    assert (await client.get("/v1/me/template")).json()["sections"] == expected

    every = {"mapping": [{"index": 1, "section": key} for key in expected[-2:]]}
    assert (
        await client.patch(
            "/v1/me/template/mapping", json={"mapping": every["mapping"][:1]}, headers={"if-match": up.headers["etag"]}
        )
    ).status_code == 200
