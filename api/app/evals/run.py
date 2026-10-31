"""`make eval`: run the real models against the synthetic bundles in `evals/` and fail if extraction
precision falls below `--threshold` (architecture 6, api/AGENTS.md).

The live Bedrock and Azure clients arrive with the cloud cell. Until the cell config names them,
the run stops with exit 2 and says it needs cloud accounts. It prints counts only, never bundle text
(rule 6).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from app.core.config import ConfigError, cell_settings

NEEDS_CLOUD = 2


def _threshold(value: str) -> float:
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise argparse.ArgumentTypeError("the threshold is a share between 0 and 1")
    return number


def _links(env: Mapping[str, str] | None) -> int:
    """T-11 recall@10 with the cell's embedder (and reranker, if configured) against `links.FLOOR`."""
    from app.core.ai.embed import embedder_from_env
    from app.core.ai.rerank import reranker_from_env
    from app.core.packs import REPO
    from app.evals import links

    expected = REPO / "evals" / "gb-eaw" / "clin-neg-breach" / "general-practice" / "expected"
    source = os.environ if env is None else env
    folders = {"EMBEDDER": "bge-small-en-v1.5-int8", "RERANKER": "ms-marco-minilm-l6-int8"}
    models_dir = Path(source.get("MODELS_DIR", "/opt/models"))
    absent = [
        f"{folder}/{name}"
        for setting, folder in folders.items()
        if source.get(setting, "").strip() == "onnx"
        for name in ("model_quantized.onnx", "tokenizer.json")
        if not (models_dir / folder / name).is_file() or (models_dir / folder / name).stat().st_size == 0
    ]
    if absent:
        print(
            f"make eval --task links needs the model files in MODELS_DIR (scripts/fetch_models.py): missing {absent}",
            file=sys.stderr,
        )
        return NEEDS_CLOUD
    try:
        result = links.evaluate(embedder_from_env(env), expected, reranker=reranker_from_env(env))
    except ConfigError as problem:
        print(f"make eval --task links needs EMBEDDER (recorded or onnx): {problem}", file=sys.stderr)
        return NEEDS_CLOUD
    scores = [round(r, 2) for r in result.per_question]
    print(f"links recall@{links.K} = {result.recall:.2f} (floor {links.FLOOR:.2f}); per question {scores}")
    return 0 if result.recall >= links.FLOOR else 1


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.evals.run", description=__doc__)
    parser.add_argument("--threshold", type=_threshold, default=0.95, help="lowest extraction precision allowed")
    parser.add_argument("--task", choices=["extraction", "links"], default="extraction")
    args = parser.parse_args(argv)
    if args.task == "links":
        return _links(env)
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
