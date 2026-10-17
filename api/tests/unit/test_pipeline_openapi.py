"""The bundle summary's documents are typed in the OpenAPI schema, so the web uses the generated client."""

from __future__ import annotations

from app.main import create_app


def test_the_document_schema_lists_its_fields() -> None:
    schema = create_app().openapi()["components"]["schemas"]["Document"]
    props = schema["properties"]
    for name in (
        "id",
        "kind",
        "filename",
        "pages",
        "first_page_no",
        "last_page_no",
        "received_on",
        "obtained_how",
        "bundle_version",
        "media_type",
        "part_of",
    ):
        assert name in props, name
    assert {"id", "kind", "filename", "bundle_version"} <= set(schema["required"])
    assert not {"media_type", "part_of"} & set(schema["required"]), "these appear once a file is sniffed"
