"""`rows`: words that stand on one row of the page print on one line, whatever order the scan read them in
(no database). Labels in a left column and their values in a right column come out side by side."""

from __future__ import annotations

from app.features.reader.transcript import rows


def box(x: float, y: float, w: float = 60.0, h: float = 24.0) -> list[float]:
    return [x, y, x + w, y, x + w, y + h, x, y + h]


def test_a_label_column_read_first_and_its_value_column_print_side_by_side() -> None:
    boxes = [
        ("problem", box(1.0, 10)),
        ("history", box(1.0, 50)),
        ("plan", box(1.0, 90)),
        ("uti", box(2.5, 12)),
        ("dysuria", box(2.5, 48)),
        ("fluids", box(2.5, 91)),
    ]
    assert rows(boxes) == [["problem", "uti"], ["history", "dysuria"], ["plan", "fluids"]]


def test_words_on_a_row_run_left_to_right_whatever_the_reading_order() -> None:
    assert rows([("b", box(200, 10)), ("a", box(10, 11)), ("c", box(400, 9))]) == [["a", "b", "c"]]


def test_a_gap_of_more_than_half_the_taller_word_starts_a_new_row() -> None:
    assert rows([("a", box(10, 0)), ("b", box(100, 13))]) == [["a"], ["b"]]  # 13 apart on a 24 high word
    assert rows([("a", box(10, 0)), ("b", box(100, 11))]) == [["a", "b"]]


def test_a_word_with_no_box_keeps_its_place_on_a_line_of_its_own() -> None:
    got = rows([("a", box(10, 0)), ("x", []), ("b", box(10, 50)), ("c", box(100, 50))])
    assert got == [["a"], ["x"], ["b", "c"]]


def test_nothing_gives_no_rows() -> None:
    assert rows([]) == []
