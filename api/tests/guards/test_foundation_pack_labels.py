"""Rule 11: every fact kind and every field of it, in every pack (launch and fixture), has a label in
the pack's own words, so no screen falls back to showing a key."""

from pathlib import Path

import pytest
import yaml

PACKS = Path(__file__).resolve().parents[3] / "packs"
FACT_FILES = sorted(PACKS.rglob("facts.yaml"))


def test_both_launch_and_fixture_facts_are_found() -> None:
    assert any("_fixtures" in p.parts for p in FACT_FILES)
    assert any("_fixtures" not in p.parts for p in FACT_FILES)


@pytest.mark.parametrize("path", FACT_FILES, ids=lambda p: str(p.relative_to(PACKS)))
def test_every_fact_kind_and_field_has_a_label(path: Path) -> None:
    kinds = yaml.safe_load(path.read_text())["fact_kinds"]
    missing: list[str] = []
    for kind in kinds:
        if not str(kind.get("label") or "").strip():
            missing.append(kind["key"])
        labels = kind.get("field_labels") or {}
        missing += [f"{kind['key']}.{f}" for f in kind.get("fields", {}) if not str(labels.get(f) or "").strip()]
        missing += [f"{kind['key']}.{f} (no such field)" for f in labels if f not in kind.get("fields", {})]
    assert not missing, f"no label for: {', '.join(missing)}"
