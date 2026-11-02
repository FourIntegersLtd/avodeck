"""Settings read from the environment through three helpers; a bad value is a ConfigError naming the setting."""

from __future__ import annotations

import pytest

from app.core.config import ConfigError, env_bool, env_csv, env_int


def test_env_int_reads_a_number_or_the_default() -> None:
    assert env_int({"N": " 7 "}, "N", 4) == 7
    assert env_int({}, "N", 4) == 4
    assert env_int({"N": ""}, "N", 4) == 4


@pytest.mark.parametrize("value", ["abc", "1.5", "0", "-2"])
def test_env_int_refuses_what_is_not_a_positive_whole_number(value: str) -> None:
    with pytest.raises(ConfigError, match="N"):
        env_int({"N": value}, "N", 4, minimum=1)


@pytest.mark.parametrize(("value", "expected"), [("1", True), ("true", True), (" TRUE ", True), ("yes", True)])
def test_env_bool_true_words(value: str, expected: bool) -> None:
    assert env_bool({"B": value}, "B") is expected


@pytest.mark.parametrize("value", ["", "0", "false", "False", "no"])
def test_env_bool_false_words(value: str) -> None:
    assert env_bool({"B": value}, "B") is False
    assert env_bool({}, "B") is False


def test_env_bool_refuses_a_word_it_does_not_know() -> None:
    with pytest.raises(ConfigError, match="B"):
        env_bool({"B": "maybe"}, "B")


def test_env_csv_splits_trims_and_drops_empties() -> None:
    assert env_csv({"L": " a, b ,,c "}, "L") == ("a", "b", "c")
    assert env_csv({}, "L") == ()
