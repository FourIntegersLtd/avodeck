"""A case whose subject has a recorded name, other name and date of birth, and text that carries them.

Used by the tests that prove no identifier reaches a model (rule 7). The values are synthetic.
"""

from __future__ import annotations

import re
import uuid
from datetime import date

import psycopg

from app.core.crypto import CaseCrypto
from app.features.setup.identity import save_identity

NAME = "Jane Testperson"
OTHER_NAME = "Jane Oldname"
BORN = date(1960, 2, 1)
PARTY = "Zebedee Quillfeather"

# Strings that must never reach a model once the identity is recorded (compared lower-cased).
FORBIDDEN = ("testperson", "oldname", "01/02/1960", "1 february 1960", "01 feb 1960", "1960-02-01", "quillfeather")

# One sentence with every form of the identifiers, for a page or a request.
SENTENCE = (
    "Jane Testperson (also Jane Oldname) born 01/02/1960, again 1 February 1960 or 01 Feb 1960."
    " Seen with Zebedee Quillfeather."
)


async def record_identity(aconn: psycopg.AsyncConnection, crypto: CaseCrypto, case: uuid.UUID) -> None:
    await save_identity(aconn, crypto, case, name=NAME, other_names=[OTHER_NAME], date_of_birth=BORN)
    sealed = await crypto.encrypt(aconn, case, PARTY.encode(), purpose="party_name")
    await aconn.execute(
        "insert into app.case_parties (case_id, role_key, name, position) values (%s, 'party_a', %s, 0)",
        (case, sealed),
    )


# Prompts that send one word per line start each line with an id; the id and the line breaks are not part of the words.
_WORD_ID = re.compile(r"(?m)^[A-Za-z]+\d+(?:[A-Za-z]+\d+)*[\t ]")


def leaked(texts: list[str]) -> list[str]:
    """Which forbidden strings appear in any text (the strings are synthetic)."""
    folded = [" ".join(_WORD_ID.sub("", t).casefold().split()) for t in texts]
    return [bad for bad in FORBIDDEN if any(bad in t for t in folded)]
