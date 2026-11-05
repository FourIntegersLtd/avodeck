"""The load-test scripts (scalability plan PR 18) cannot touch production, hold no secrets and log no patient
text; the capacity document carries the targets; the retired DigitalOcean files are gone (ADR 0021).

The staging guard is plain JavaScript (`scripts/load/lib/target.js`), run here with Node.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
LOAD = REPO / "scripts" / "load"
TARGET = LOAD / "lib" / "target.js"
SCENARIOS = ("bundles.js", "interactive.js", "experts.js")

ACCEPTED = [
    "https://api-staging.up.railway.app",
    "https://avodeck-api-staging.up.railway.app/",
    "https://uk.staging.api.avodeck.com",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]
REFUSED = [
    "",
    "https://uk.api.avodeck.com",
    "https://api.avodeck.com/v1",
    "https://api-production.up.railway.app",
    "https://api-staging-production.up.railway.app",  # a production environment named like staging
    "http://api-staging.up.railway.app",  # staging over plain http
    "https://staging.evil.com",
    "https://uk.api.avodeck.com.staging.evil.com",
    "https://staging.up.railway.app.evil.com",
    "https://user:secret@api-staging.up.railway.app",
    "https://api-staging.up.railway.app:8443@evil.com",
    "https://evilstaging.up.railway.app",
    "https://api-staging.up.railway.app/v1/cases",  # a path: give the origin only
    "not a url",
    "ftp://api-staging.up.railway.app",
]


def _check(url: str | None) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    env = {} if url is None else {"STAGING_URL": url}
    module, given = json.dumps(TARGET.as_uri()), json.dumps(env)
    script = (
        "import {requireStaging} from " + module + ";"
        "let out;try{out={ok:true,base:requireStaging(" + given + ")}}"
        "catch(e){out={ok:false,error:String(e.message)}}"
        "console.log(JSON.stringify(out))"
    )
    done = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(done.stdout)  # type: ignore[no-any-return]


@pytest.mark.parametrize("url", ACCEPTED)
def test_staging_hosts_are_accepted(url: str) -> None:
    out = _check(url)
    assert out["ok"], out
    assert not str(out["base"]).endswith("/")


@pytest.mark.parametrize("url", REFUSED)
def test_anything_else_is_refused(url: str) -> None:
    out = _check(url)
    assert out["ok"] is False, f"{url!r} was accepted"
    assert not url or url not in str(out["error"]), "the error must not echo the address back"


def test_a_missing_staging_url_is_refused() -> None:
    assert _check(None)["ok"] is False


def test_every_scenario_goes_through_the_guard() -> None:
    for name in SCENARIOS:
        text = (LOAD / name).read_text()
        assert 'from "./lib/common.js"' in text, f"{name} must import lib/common.js (which calls requireStaging)"
    common = (LOAD / "lib" / "common.js").read_text()
    assert "requireStaging(__ENV)" in common
    assert "__ENV.STAGING_URL" not in "".join((LOAD / n).read_text() for n in SCENARIOS), (
        "scenarios must take the base address from lib/common.js, never from the environment directly"
    )


def test_no_secret_or_production_address_in_the_scripts() -> None:
    secret = re.compile(
        r"(eyJ[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{12,}|-----BEGIN|password\s*[:=]\s*['\"][^'\"]{3,})"
    )
    production = re.compile(r"https?://[a-z0-9.-]*avodeck\.com", re.I)
    for path in LOAD.rglob("*"):
        if path.suffix not in {".js", ".md", ".json", ".sh"}:
            continue
        text = path.read_text()
        assert not secret.search(text), f"{path.name} looks like it holds a secret"
        if path.name != "README.md":
            found = production.findall(text)
            assert not found, f"{path.name} names a real address: {found}"


def test_scripts_never_print_bodies_or_credentials() -> None:
    printing = re.compile(r"console\.(log|info|warn|error|debug)\(([^)]*)\)")
    for path in LOAD.rglob("*.js"):
        for _, args in printing.findall(path.read_text()):
            assert not re.search(r"body|json\(\)|password|email|text|cookie|token|csrf", args, re.I), (
                f"{path.name} prints something that may be patient text or a credential: {args}"
            )


def test_load_readme_states_the_safety_rules() -> None:
    text = (LOAD / "README.md").read_text()
    for phrase in ("STAGING_URL", "never against production", "synthetic", "k6 run"):
        assert phrase in text, phrase


def test_capacity_document_has_the_run_table_with_empty_results() -> None:
    text = (REPO / "docs" / "ops" / "capacity.md").read_text()
    assert "## Load test (scalability plan PR 18)" in text
    section = text.split("## Load test (scalability plan PR 18)", 1)[1]
    for target in ("75 min", "400 ms", "300 ms", "30 s", "0.1%", "0.5%", "100 ms", "80%", "5%"):
        assert target in section, f"target {target} missing from the run table"
    rows = [r for r in section.splitlines() if r.startswith("| ") and "Result" not in r and "---" not in r]
    assert len(rows) >= 7, "one row per run: 5/20/50 bundles, interactive, experts, soak"
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert cells[-1] == "" and cells[-2] == "", f"result columns must be left empty for the user: {row}"


def test_retired_digitalocean_files_are_gone() -> None:
    assert not (REPO / ".do").exists()
    assert not (REPO / "Dockerfile").exists(), "the one image is api/Dockerfile (ADR 0021)"


def test_no_stale_digitalocean_wording() -> None:
    """Outside the research notes (history) and decision records, nothing names the old host. A decision record that
    does must say it was replaced by ADR 0021."""
    skip_dirs = {".git", "node_modules", ".venv", "evals", "docs/research", "web/dist"}
    allowed_files = {
        "AGENTS.md",  # wording change awaits the founder's approval
        "docs/ops/scalability-plan.md",
        "docs/decisions/0021-api-and-worker-on-railway-amsterdam.md",
    }
    own = Path(__file__).resolve()
    found: list[str] = []
    listed = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    for rel in listed:
        path = REPO / rel
        if path.resolve() == own or not path.is_file() or any(rel == d or rel.startswith(d + "/") for d in skip_dirs):
            continue
        if path.suffix not in {".md", ".py", ".yaml", ".yml", ".json", ".toml", ".example", ".sh", ".ts", ".tsx", ""}:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if not re.search(r"digital ?ocean|\.do/app", text, re.I) or rel in allowed_files:
            continue
        if rel.startswith("docs/decisions/") and "0021" in text:
            continue
        found.append(rel)
    assert not found, f"DigitalOcean wording left in: {found}"
