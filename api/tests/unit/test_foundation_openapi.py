"""`python -m app.export_openapi` prints the API's OpenAPI document (`make api-client`).

The committed `openapi.json` at the repository root is what the frontend client is generated from,
so it must match the app: regenerate it with `make -C api openapi` after changing a route.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.export_openapi import openapi_json
from app.main import create_app

REPO = Path(__file__).resolve().parents[3]


def test_the_export_is_the_apps_openapi_document() -> None:
    doc = json.loads(openapi_json())
    assert doc == json.loads(json.dumps(create_app().openapi()))
    assert "/v1/today" in doc["paths"]
    assert openapi_json() == openapi_json(), "the export is stable, so diffs show only real changes"


def test_the_export_runs_as_a_module() -> None:
    out = subprocess.run(
        [sys.executable, "-m", "app.export_openapi"],
        cwd=REPO / "api",
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout
    assert json.loads(out)["info"]["title"] == "Avodeck API"


def test_the_committed_openapi_json_is_up_to_date() -> None:
    committed = REPO / "openapi.json"
    assert committed.exists(), "run `make -C api openapi` and commit openapi.json"
    assert json.loads(committed.read_text()) == json.loads(openapi_json()), "run `make -C api openapi`"


def test_the_committed_pack_fixtures_for_the_web_tests_are_up_to_date() -> None:
    """Web tests run screen copy under the launch and fixture packs (rule 11) from real pack data."""
    from app.export_openapi import pack_fixtures_json

    data = json.loads(pack_fixtures_json())
    assert set(data) == {"launch", "fixture"}
    assert [s["key"] for s in data["fixture"]["case_pack"]["workflow_steps"]] == ["gather", "review", "conclude"]
    assert data["launch"]["case_pack"]["locale"] == "en-GB"
    assert data["fixture"]["jurisdiction"]["code"] == "zz-test"
    committed = REPO / "web/src/foundation/platform-client/pack-fixtures.json"
    assert committed.exists(), "run `make api-client` and commit pack-fixtures.json"
    assert json.loads(committed.read_text()) == data, "run `make api-client`"
