"""Phone, hospital/record number and labelled-NHS recognisers in the launch and fixture identifier modules."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gb = _load("packs/jurisdiction/gb-eaw/identifiers.py", "gb_ids")
zz = _load("packs/_fixtures/jurisdiction/zz-test/identifiers.py", "zz_ids")


def found(mod, text: str, kind: str) -> list[str]:
    return [text[s:e] for s, e, k in mod.find_identifiers(text) if k == kind]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Call 0123 456 7890", "0123 456 7890"),
        ("Call +44 20 7946 0958", "+44 20 7946 0958"),
        ("Call 07700 900000", "07700 900000"),
    ],
)
def test_phone_numbers_masked(text, expected):
    assert found(gb, text, "PHONE_NUMBER") == [expected]


@pytest.mark.parametrize(
    "text",
    ["Call 01onal234 5678", "Call 01onal2345678", "Call 01onal 234 5678", "£1,234,567", "in 2023 4500 patients"],
)
def test_not_phone_numbers(text):
    assert found(gb, text, "PHONE_NUMBER") == []


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hospital No: AB12345", "AB12345"),
        ("MRN: 987654", "987654"),
        ("Record Number 12-34/56", "12-34/56"),
        ("Patient ID: P00123", "P00123"),
    ],
)
def test_hospital_numbers_mask_value_only(text, expected):
    assert found(gb, text, "HOSPITAL_NUMBER") == [expected]


def test_hospital_word_without_number():
    assert found(gb, "The hospital was busy", "HOSPITAL_NUMBER") == []


@pytest.mark.parametrize(
    "text,expected",
    [("NHS Number: 943 476 5871", "943 476 5871"), ("NHS No 1234567890", "1234567890")],
)
def test_bad_checksum_near_nhs_label_masked(text, expected):
    assert not gb.valid_nhs_number(expected.replace(" ", ""))
    assert found(gb, text, "NHS_NUMBER") == [expected]


def test_bad_checksum_without_label_not_masked():
    assert found(gb, "1234567890 in the budget", "NHS_NUMBER") == []


def test_valid_nhs_number_reported_once():
    assert found(gb, "NHS Number: 943 476 5870", "NHS_NUMBER") == ["943 476 5870"]


def test_fixture_phone_and_hospital():
    text = "Ring 555-1234 about ZZ-HOSP-0042 and ZZ-ID-123456"
    assert found(zz, text, "ZZ_PHONE") == ["555-1234"]
    assert found(zz, text, "ZZ_HOSPITAL") == ["ZZ-HOSP-0042"]
    assert found(zz, text, "ZZ_ID") == ["ZZ-ID-123456"]
