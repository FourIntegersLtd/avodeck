"""Slice G: a method a literal path lacks answers 405, before sign-in (Schemathesis "unsupported methods").

`PATCH /written-questions/import` must not match `PATCH /written-questions/{question_id}`, where sign-in
would run first and answer 401.
"""

# ruff: noqa: F811
from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI

from tests.fixtures.test_after_serving_world import (  # noqa: F401
    app,
    combo,
    mailer,
    model,
    ocr,
    reads,
    services,
    storage,
)

pytestmark = pytest.mark.anyio

CASE = uuid.uuid4()
LACKING = [
    ("PATCH", f"/v1/cases/{CASE}/written-questions/import"),
    ("PATCH", f"/v1/cases/{CASE}/report-changes/supplementary"),
    ("GET", f"/v1/cases/{CASE}/report-changes/supplementary"),
]


@pytest.mark.parametrize(("method", "path"), LACKING)
async def test_a_method_the_literal_path_lacks_is_405(app: FastAPI, method: str, path: str) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.request(method, path, json={})
    assert resp.status_code == 405, (method, path, resp.status_code)


async def test_a_real_id_still_reaches_sign_in(app: FastAPI) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(f"/v1/cases/{CASE}/written-questions/{uuid.uuid4()}", json={})
    assert resp.status_code == 401
