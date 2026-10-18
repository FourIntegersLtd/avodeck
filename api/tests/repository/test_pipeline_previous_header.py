"""T-07b is given the top of the previous page of the same document as context (architecture 6.3).

A page that carries on a letter then knows where the letter came from and when. Page 1 has no
previous page, so its request carries no header. Words of the header are context only: they have no
IDs, so a fact can never cite them.
"""

from __future__ import annotations

import pytest

from tests.repository.test_pipeline_language import ENGLISH, WELSH, h, storage  # noqa: F401
from tests.repository.test_pipeline_support import Harness, t07b_requests

pytestmark = pytest.mark.anyio

MARK = "Previous page header"


async def test_a_page_carries_the_top_of_the_page_before_it(h: Harness) -> None:
    by_first_word = {("annwyl" in r.text): r for r in t07b_requests(h) if "annwyl" in r.text or "cough" in r.text}
    page_1, page_2 = by_first_word[False], by_first_word[True]
    assert MARK not in page_1.text, "the first page was given a header"
    assert MARK in page_2.text
    header = page_2.text.split(MARK, 1)[1].split("\n", 1)[1]
    assert "Synthetic alpha entry seen today" in header.split("\n\n")[0], "page 1's top was not passed on"
    assert ENGLISH[-1].split()[-1] not in header.split("\n\n")[0], "more than the top of page 1 was passed on"
    assert "annwyl" not in header.split("\n\n")[0]
    assert WELSH[0].split()[1] in page_2.text.split(MARK)[1]
