"""`POST /v1/uploads/{id}/complete` declares its two answers as two plain responses, not a mixed union."""

from __future__ import annotations

import json
from typing import Any

from app.export_openapi import openapi_json


def test_complete_declares_the_document_as_200_and_the_wait_as_202() -> None:
    doc = json.loads(openapi_json())
    responses: dict[str, Any] = doc["paths"]["/v1/uploads/{upload_id}/complete"]["post"]["responses"]
    assert responses["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/UploadedDocument"
    }
    assert responses["202"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Finalizing"}
