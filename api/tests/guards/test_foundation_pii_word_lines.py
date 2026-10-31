"""Rule 7: stripping does not depend on how the words are laid out.

Some prompts send one word per line, `<id><tab or space><word>` (T-07b: `p1w5 Jane`; the agenda: `w36<tab>Jane`).
A known date of birth in any written form and a name spread over lines are still removed, each word keeps its
id, and the words around them are untouched. Values are made up.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.ai import pii
from app.core.packs import MergedPack, default_library
from tests.support.packs import LAUNCH

BORN = date(1960, 2, 1)
NAMES = ["Jane Testperson", "Jane Oldname"]


@pytest.fixture(scope="module")
def pack() -> MergedPack:
    return default_library().combine(*LAUNCH.codes)


def laid_out(sentence: str, sep: str, prefix: str = "w") -> str:
    return "\n".join(f"{prefix}{n}{sep}{word}" for n, word in enumerate(sentence.split(), start=1))


@pytest.mark.parametrize("sep", ["\t", " "])
@pytest.mark.parametrize(
    "written", ["1 February 1960", "01 Feb 1960", "1st of February 1960", "February 1, 1960", "01/02/1960"]
)
def test_a_date_of_birth_over_several_lines_is_removed_and_ids_stay(pack: MergedPack, sep: str, written: str) -> None:
    text = laid_out(f"Seen on 3 May 2021. Born {written} and well", sep)
    out = pii.strip(text, pack=pack, dates_of_birth=[BORN]).text
    assert "1960" not in out
    assert "May" in out and "2021." in out, "an event date was removed"
    ids = [line.split(sep, 1)[0] for line in out.split("\n")]
    assert ids == [f"w{n}" for n in range(1, len(ids) + 1)], "every id stays, in order"
    assert "Born" in out and "well" in out.split("\n")[-1]


@pytest.mark.parametrize("sep", ["\t", " "])
def test_a_name_over_several_lines_is_removed_and_ids_stay(pack: MergedPack, sep: str) -> None:
    text = laid_out("Seen with Jane Testperson (also Jane Oldname) today", sep, prefix="p1w")
    out = pii.strip(text, pack=pack, names=NAMES).text
    assert "Jane" not in out and "Testperson" not in out and "Oldname" not in out
    assert out.count("\n") == text.count("\n"), "one line per word, as before"
    assert "Seen" in out and "today" in out


def test_text_that_is_not_word_per_line_is_unchanged_by_the_layout_rule(pack: MergedPack) -> None:
    text = "Patient Jane Testperson was born on 1 February 1960.\nSeen again later."
    out = pii.strip(text, pack=pack, names=NAMES, dates_of_birth=[BORN]).text
    assert out == "Patient [NAME_1] was born on [DATE_OF_BIRTH_1].\nSeen again later."
