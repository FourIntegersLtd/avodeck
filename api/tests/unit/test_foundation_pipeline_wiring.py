"""Slice A is mounted: its routes are in the app and its jobs and upload subscriber are in the worker."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[2]


def _run(code: str) -> object:
    out = subprocess.run([sys.executable, "-c", code], cwd=API, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_app_serves_the_pipeline_routes() -> None:
    paths = _run(
        "import json; from app.main import create_app;"
        " print(json.dumps(sorted(getattr(r, 'path', '') for r in create_app().routes)))"
    )
    assert isinstance(paths, list)
    assert "/v1/cases/{case_id}/duplicates" in paths
    assert "/v1/cases/{case_id}/pages/{page_id}/duplicate" in paths


def test_the_worker_knows_the_pipeline_jobs_and_subscribes_to_uploads() -> None:
    seen = _run(
        "import json; import app.worker.main; from app.core import jobs;"
        " from app.core.contracts.events import DocumentUploaded, events;"
        " print(json.dumps({'kinds': sorted(jobs.registry.kinds),"
        " 'subscribers': len(events._in_transaction[DocumentUploaded])}))"
    )
    assert isinstance(seen, dict)
    assert {"scan_document", "process_bundle", "process_pages"} <= set(seen["kinds"])
    assert seen["subscribers"] >= 1
