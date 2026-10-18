"""Slices meet only through core: no feature package imports another, and none reaches into core's private names.

A slice writes only its own code and reads other slices' data through `app/core/contracts`
(AGENTS.md, Slices and ownership). An import from `app.features.<other>` couples two slices so
that one cannot change without breaking the other; a private core name (`module._name`) is not a
contract and can change without notice.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

FEATURES = Path(__file__).resolve().parents[2] / "app" / "features"
PRIVATE_CORE = re.compile(r"\b(pii|provenance|packs|crypto|audit|jobs)\._[a-z]\w*")


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
    return names


def test_no_feature_imports_another_feature() -> None:
    crossings = []
    for path in sorted(FEATURES.rglob("*.py")):
        own = path.relative_to(FEATURES).parts[0]
        for name in _imports(path):
            parts = name.split(".")
            if parts[:2] == ["app", "features"] and len(parts) > 2 and parts[2] != own:
                crossings.append(f"{path.relative_to(FEATURES)}: {name}")
    assert crossings == [], f"read other slices through app/core/contracts: {crossings}"


def test_no_feature_uses_a_private_core_name() -> None:
    found = [
        f"{path.relative_to(FEATURES)}: {m.group(0)}"
        for path in sorted(FEATURES.rglob("*.py"))
        for m in PRIVATE_CORE.finditer(path.read_text())
    ]
    assert found == [], f"ask the foundation for a public name: {found}"
