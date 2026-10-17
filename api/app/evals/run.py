"""`make eval`: run the real models against the synthetic bundles in `evals/` and fail if extraction
precision falls below `--threshold` (architecture 6, api/AGENTS.md).

The live Bedrock and Azure clients arrive with the cloud cell. Until the cell config names them,
the run stops with exit 2 and says it needs cloud accounts. It prints counts only, never bundle text
(rule 6).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence

from app.core.config import ConfigError, cell_settings

NEEDS_CLOUD = 2


def _threshold(value: str) -> float:
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise argparse.ArgumentTypeError("the threshold is a share between 0 and 1")
    return number


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.evals.run", description=__doc__)
    parser.add_argument("--threshold", type=_threshold, default=0.95, help="lowest extraction precision allowed")
    parser.parse_args(argv)
    try:
        cell_settings(env)
    except ConfigError as missing:
        print(
            f"make eval needs cloud accounts: the cell config is incomplete ({type(missing).__name__}).",
            file=sys.stderr,
        )
        return NEEDS_CLOUD
    print("make eval needs cloud accounts: the live model and reading clients are not configured yet.", file=sys.stderr)
    return NEEDS_CLOUD


if __name__ == "__main__":
    sys.exit(main())
