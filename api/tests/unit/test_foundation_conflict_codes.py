"""Two 409 answers the screens tell apart: a confirmed question cannot change; the question set is locked."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.core import errors


@pytest.mark.parametrize("make", [errors.already_confirmed, errors.questions_locked])
def test_each_is_a_409_with_its_own_code_and_a_fixed_message(make: Callable[[], errors.ApiError]) -> None:
    error = make()
    assert isinstance(error, errors.ApiError)
    assert error.status == 409
    assert error.code == make.__name__
    assert error.message and "{" not in error.message


def test_the_codes_differ_from_each_other_and_from_conflict() -> None:
    codes = {errors.already_confirmed().code, errors.questions_locked().code, errors.conflict().code}
    assert len(codes) == 3
