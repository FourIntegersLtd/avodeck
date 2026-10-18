"""Bank details on a billing profile (screen 18b): optional, checked loosely, printed in the invoice's
"How to pay" box from the issuer snapshot taken at draft time and never put in any email.

Spaces and dashes are dropped and letters put in capitals before the loose checks (sort code 6 digits,
account number 4-20 digits, an IBAN's shape, an 8- or 11-character SWIFT/BIC). Billing profiles are not case
data, so the details are plain columns (not sealed with the case key).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

FIELDS = ("account_name", "sort_code", "account_number", "iban", "bic")
COLUMNS = ", ".join(f"bank_{f}" for f in FIELDS)
_PATTERNS = {
    "sort_code": r"^[0-9]{6}$",
    "account_number": r"^[0-9]{4,20}$",
    "iban": r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$",
    "bic": r"^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$",
}


class BankDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_name: str | None = Field(default=None, max_length=140)
    sort_code: str | None = None
    account_number: str | None = None
    iban: str | None = None
    bic: str | None = None

    @field_validator("account_name", mode="before")
    @classmethod
    def _name(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("sort_code", "account_number", "iban", "bic", mode="before")
    @classmethod
    def _code(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = re.sub(r"[\s-]", "", value).upper()
            return value or None
        return value

    @field_validator("sort_code", "account_number", "iban", "bic")
    @classmethod
    def _shape(cls, value: str | None, info: Any) -> str | None:
        if value is not None and not re.fullmatch(_PATTERNS[info.field_name], value):
            raise ValueError("does not look right")
        return value

    def filled(self) -> bool:
        return any(getattr(self, f) is not None for f in FIELDS)


def from_row(row: dict[str, Any]) -> BankDetails | None:
    """The bank details of a profile row (its `bank_*` columns), or None when none are filled in."""
    bank = BankDetails(**{f: row.get(f"bank_{f}") for f in FIELDS})
    return bank if bank.filled() else None


def snapshot(row: dict[str, Any]) -> dict[str, str]:
    """The filled-in details for the issuer snapshot (frozen into the invoice when drafted)."""
    return {f: str(row[f"bank_{f}"]) for f in FIELDS if row.get(f"bank_{f}") is not None}
