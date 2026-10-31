"""Rule 4 and rule 5: case files stay in the home region and never get a direct browser link (ADR 0032).

Settings refuse a region outside the cell's allow-list; the local S3 endpoint override is refused
unless it points at this machine, so a test setting can never reach production; and nothing in the
app can make a presigned or public object link.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import ConfigError
from app.core.storage import CaseFileStorage, storage_settings
from tests.support.files import STORAGE_REGION

API = Path(__file__).resolve().parents[2]

CELL = {
    "STORAGE_BUCKET": "case-files-test",
    "AWS_REGION": STORAGE_REGION,
    "AWS_ALLOWED_REGIONS": STORAGE_REGION,
    "AWS_ACCESS_KEY_ID": "test-access-key",
    "AWS_SECRET_ACCESS_KEY": "test-secret-key",
}


def test_the_cells_region_is_accepted() -> None:
    settings = storage_settings(dict(CELL))
    assert settings.region == STORAGE_REGION
    assert CaseFileStorage(settings).settings.region == STORAGE_REGION


@pytest.mark.parametrize("region", ["us-east-1", "eu-west-1", "eu-central-1", "eu-west-3", ""])
def test_settings_refuse_a_region_outside_the_cell(region: str) -> None:
    with pytest.raises(ConfigError):
        storage_settings({**CELL, "AWS_REGION": region})


@pytest.mark.parametrize("name", ["STORAGE_BUCKET", "AWS_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"])
def test_settings_need_every_required_value(name: str) -> None:
    env = {**CELL, "STORAGE_BUCKET": "case-files-test"}
    env[name] = ""
    if name == "STORAGE_BUCKET":
        assert storage_settings(env).bucket == "case-files"  # the one value with a default
        return
    with pytest.raises(ConfigError):
        storage_settings(env)


@pytest.mark.parametrize("url", ["http://127.0.0.1:5000", "http://localhost:5000", "http://[::1]:5000"])
def test_a_local_endpoint_override_is_accepted(url: str) -> None:
    assert storage_settings({**CELL, "S3_ENDPOINT_URL": url}).endpoint_url == url


@pytest.mark.parametrize(
    "url",
    [
        "https://s3.eu-west-2.amazonaws.com",
        "https://example.com",
        "http://localhost.evil.example",
        "http://127.0.0.1.evil.example",
        "http://10.0.0.5:9000",
        "http://user@evil.example/",
    ],
)
def test_an_endpoint_override_that_is_not_local_is_refused(url: str) -> None:
    with pytest.raises(ConfigError):
        storage_settings({**CELL, "S3_ENDPOINT_URL": url})


def test_no_presigned_or_public_object_link_is_written_in_the_app() -> None:
    pattern = re.compile(
        r"presign|generate_presigned|\bs3[.-][a-z0-9.-]*amazonaws\.com|amazonaws\.com/|"
        r"\.s3\.[a-z0-9-]*\.?amazonaws|x-amz-signature|PublicAccessBlock.*False|ACL\s*=\s*[\"']public",
        re.IGNORECASE,
    )
    files = sorted((API / "app").rglob("*.py"))
    assert len(files) > 100
    found = [str(f.relative_to(API)) for f in files if pattern.search(f.read_text())]
    assert found == []
