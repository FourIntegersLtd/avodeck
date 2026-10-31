"""Rule 7: identifiers out before AI (architecture 6.1 M9, 6.2.1).

`pii.strip` removes the names on the case's name list, the identifiers the case's jurisdiction pack
recognises (a modulus-11 check-digit number and a postcode under the launch pack, a `ZZ-ID-`
number under the fixture pack) and dates of birth, labelled or known. Dates of events stay: the
model needs them. Every value below is made up.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.ai import pii
from app.core.packs import MergedPack, PackLibrary, default_library
from tests.support.ai import NAME, mod11_number, spaced
from tests.support.packs import FIXTURE, LAUNCH, Combo


@pytest.fixture(scope="module")
def library() -> PackLibrary:
    return default_library()


def merged(library: PackLibrary, combo: Combo) -> MergedPack:
    return library.combine(*combo.codes)


def assert_gone(text: str, *values: str) -> None:
    for value in values:
        assert value.lower() not in text.lower(), "an identifier survived stripping"


def test_launch_pack_names_numbers_postcodes_and_dates_of_birth_are_removed(library: PackLibrary) -> None:
    number = mod11_number("943476591")
    text = (
        f"Patient: {NAME}. Number {spaced(number)}. Address 12 High Street, ZZ9 9ZZ. DOB: 12/03/1961.\n"
        f"Seen 14/03/2020: PEMBERTON-VALE, Ada complains of cough. Mrs Pemberton-Vale advised. Ref {number}."
    )
    out = pii.strip(text, pack=merged(library, LAUNCH), names=[NAME])
    assert_gone(out.text, NAME, "Pemberton-Vale", "Ada ", spaced(number), number, "ZZ9 9ZZ", "12/03/1961")
    assert "14/03/2020" in out.text, "an event date was removed"
    assert "cough" in out.text
    assert sum(out.counts.values()) >= 7


def test_a_number_failing_the_check_digit_is_not_an_identifier(library: PackLibrary) -> None:
    out = pii.strip("Lab batch 123 456 7890 received.", pack=merged(library, LAUNCH))  # noqa: B005
    assert "123 456 7890" in out.text
    assert sum(out.counts.values()) == 0


def test_known_dates_of_birth_are_removed_in_any_written_form_and_event_dates_stay(library: PackLibrary) -> None:
    text = "Born 1961-03-12. Also written 12.03.1961 and 12 March 1961 and 12/3/61. Seen 12/03/2020 and 13/03/1961."
    out = pii.strip(text, pack=merged(library, LAUNCH), dates_of_birth=[date(1961, 3, 12)])
    assert_gone(out.text, "1961-03-12", "12.03.1961", "12 March 1961", "12/3/61")
    assert "12/03/2020" in out.text
    assert "13/03/1961" in out.text


def test_fixture_pack_identifiers_and_date_order_drive_stripping(library: PackLibrary) -> None:
    """Rule 11: the same code strips the fixture jurisdiction's identifier and reads its dates month first."""
    text = f"{NAME} ZZ-ID-123456 Birthdate: 07/04/1950. Known birthday 03/12/1961. Seen 12/03/1961 and 04/05/2021."
    out = pii.strip(text, pack=merged(library, FIXTURE), names=[NAME], dates_of_birth=[date(1961, 3, 12)])
    assert_gone(out.text, NAME, "ZZ-ID-123456", "07/04/1950", "03/12/1961")
    assert "12/03/1961" in out.text, "read month first, 12/03/1961 is not the date of birth"
    assert "04/05/2021" in out.text


def test_the_same_value_gets_the_same_placeholder(library: PackLibrary) -> None:
    out = pii.strip(f"{NAME} called. {NAME} called again.", pack=merged(library, LAUNCH), names=[NAME])
    first, second = (part.strip() for part in out.text.split("called")[:2])
    assert first.startswith("[") and first == second.lstrip(". ")


def test_stripping_result_never_shows_the_original_in_its_repr(library: PackLibrary) -> None:
    out = pii.strip(f"{NAME} seen.", pack=merged(library, LAUNCH), names=[NAME])
    assert "Pemberton" not in repr(out)


def test_the_ids_a_prompt_uses_to_name_rows_survive_stripping(library: PackLibrary) -> None:
    """A prompt names each row by its id so the model can point back at it (architecture 6.2). A digit
    run inside an id can look like a phone or record number: if it is replaced the model is shown an id
    that belongs to nothing, and its answer cannot be linked back. Both ids below are made up."""
    ids = ("add18449-b555-4514-84d1-030772676465", "3c9f0e21-7d44-4a66-9b10-020773441620")
    text = "\n".join(f"[paragraph {one}] The later visit is noted." for one in ids)
    out = pii.strip(text, pack=merged(library, LAUNCH), names=[NAME])
    for one in ids:
        assert one in out.text, "an id was mangled by stripping"
    assert sum(out.counts.values()) == 0
