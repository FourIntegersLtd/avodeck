"""Case ciphertext is bound to where it belongs (ADR 0010).

Besides the case, each sealed blob can name its purpose (for a stored file, its own storage path).
A blob opened for another purpose, such as a chunk moved to another chunk's place, is refused.
"""

from __future__ import annotations

import pytest

from app.core.crypto import CaseCrypto, CiphertextInvalid, LocalKeyProvider
from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

SECRET = b"synthetic page text, not about anyone"


async def test_a_blob_opens_only_for_the_purpose_it_was_sealed_for(api_db: Database, seed: Seed) -> None:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert)
    crypto = CaseCrypto(LocalKeyProvider.generate())
    async with api_db.request(scope_for(expert)) as conn:
        blob = await crypto.encrypt(conn, case_id, SECRET, purpose="zz/part/00001")
        assert await crypto.decrypt(conn, case_id, blob, purpose="zz/part/00001") == SECRET
        for other in ("zz/part/00002", ""):
            with pytest.raises(CiphertextInvalid):
                await crypto.decrypt(conn, case_id, blob, purpose=other)
        plain = await crypto.encrypt(conn, case_id, SECRET)
        with pytest.raises(CiphertextInvalid):
            await crypto.decrypt(conn, case_id, plain, purpose="zz/part/00001")
        assert await crypto.decrypt(conn, case_id, plain) == SECRET
