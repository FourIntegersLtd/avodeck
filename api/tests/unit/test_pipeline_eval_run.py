"""`make eval` (`python -m app.evals.run --threshold 0.95`) runs real models against the synthetic
bundles. Without cloud accounts it must stop with a clear message and a non-zero exit, not an
ImportError, and never print bundle text (rule 6)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[2]
CLOUD = ("BEDROCK_ENDPOINT", "BEDROCK_SONNET_MODEL_ID", "BEDROCK_OPUS_MODEL_ID", "AZURE_DI_ENDPOINT")


def run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.evals.run", "--threshold", "0.95"],
        cwd=API,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_without_cloud_accounts_the_eval_stops_clearly() -> None:
    env = {k: v for k, v in os.environ.items() if k not in CLOUD}
    done = run(env)
    assert done.returncode == 2, done.returncode
    assert "needs cloud accounts" in done.stderr
    assert "Traceback" not in done.stderr


def test_a_threshold_outside_zero_to_one_is_refused() -> None:
    done = subprocess.run(
        [sys.executable, "-m", "app.evals.run", "--threshold", "1.5"],
        cwd=API,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode != 0
    assert "Traceback" not in done.stderr
