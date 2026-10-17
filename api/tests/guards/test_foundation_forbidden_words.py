"""Rule 11: no slice code or prompt template contains a forbidden word (architecture 3.5, 3.6).

Scans `api/app/features`, `web/src/capabilities` and every prompt template. The word list lives in
`packs/schema/lint_words.yaml` (`slice_code_forbidden`, plus the country and specialty words).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import packs
from tests.support.packs import LAUNCH_ROOT, REPO, write

LINT_WORDS = "packs/schema/lint_words.yaml"


def test_the_forbidden_list_holds_the_words_in_architecture_3_5() -> None:
    words = packs.slice_code_forbidden_words(LAUNCH_ROOT)
    for word in (
        "CPR",
        "PD35",
        "Part 35",
        "NICE",
        "statement of truth",
        "claimant",
        "defendant",
        "solicitor",
        "GP",
        "EMIS",
        "SystmOne",
        "NHS number",
    ):
        assert word in words


def test_the_scan_covers_slice_code_and_prompt_templates(tmp_path: Path) -> None:
    write(tmp_path / LINT_WORDS, (REPO / LINT_WORDS).read_text())
    write(tmp_path / "api/app/core/ai/prompts/t07b.txt", "neutral\n")
    write(tmp_path / "api/app/features/write/prompts/t15.md", "neutral\n")
    scanned = {p.relative_to(tmp_path).as_posix() for p in packs.slice_code_files(tmp_path)}
    assert {"api/app/core/ai/prompts/t07b.txt", "api/app/features/write/prompts/t15.md"} <= scanned


def test_no_slice_code_or_prompt_template_contains_a_forbidden_word() -> None:
    findings = packs.scan_slice_code(REPO)
    assert findings == [], "\n".join(f"{f.path}:{f.line}: {f.word}" for f in findings)


def test_the_scan_finds_a_planted_word(tmp_path: Path) -> None:
    """The scan above proves nothing unless it can fail."""
    write(tmp_path / LINT_WORDS, (REPO / LINT_WORDS).read_text())
    word = packs.slice_code_forbidden_words(LAUNCH_ROOT)[0]
    write(tmp_path / "api/app/features/pipeline/service.py", f'LABEL = "ask the {word}"\n')
    write(tmp_path / "web/src/capabilities/reader/Thing.tsx", "export const ok = 1;\n")
    write(tmp_path / "api/app/core/ai/prompts/t07b.txt", "Extract every {fact_kind}. Mind the GP.\n")
    findings = packs.scan_slice_code(tmp_path)
    found = {(f.path, f.word) for f in findings}
    assert ("api/app/features/pipeline/service.py", word) in found
    assert ("api/app/core/ai/prompts/t07b.txt", "GP") in found
    assert not any(f.path.endswith("Thing.tsx") for f in findings)


@pytest.mark.parametrize("text", ["gpu_count = 1", "nice_to_have = True", "hemisphere"])
def test_the_scan_ignores_innocent_lookalikes(tmp_path: Path, text: str) -> None:
    write(tmp_path / LINT_WORDS, (REPO / LINT_WORDS).read_text())
    write(tmp_path / "api/app/features/reader/service.py", text + "\n")
    assert packs.scan_slice_code(tmp_path) == []


def test_the_scan_covers_the_shared_contracts(tmp_path: Path) -> None:
    """Shared contracts are read by every slice, so they are held to the slice-code word list too."""
    write(tmp_path / LINT_WORDS, (REPO / LINT_WORDS).read_text())
    write(tmp_path / "api/app/core/contracts/today.py", "KIND = 'neutral'\n")
    scanned = {p.relative_to(tmp_path).as_posix() for p in packs.slice_code_files(tmp_path)}
    assert "api/app/core/contracts/today.py" in scanned
