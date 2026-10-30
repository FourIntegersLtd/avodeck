"""Slice I's stored files, sealed under the case key (purpose: the storage path, so a copied object will not open).

Hashes shown to people (`X-Content-SHA256`, the signed-elsewhere hash, the court bundle's hash) are always
over the plain file; only what reaches storage is sealed. A destroyed case key makes every read raise
`CaseKeyDestroyed`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from psycopg import AsyncConnection

from app.core.crypto import CaseCrypto
from app.core.storage import SupabaseStorage


class Vault(Protocol):
    """What sealing needs: `TrialServices` has both, and so does `Sealer`."""

    def case_crypto(self) -> CaseCrypto: ...

    def files(self) -> SupabaseStorage: ...


@dataclass(frozen=True)
class Sealer:
    """The crypto and storage a route was given, in the shape the helpers take."""

    crypto: CaseCrypto
    storage: SupabaseStorage

    def case_crypto(self) -> CaseCrypto:
        return self.crypto

    def files(self) -> SupabaseStorage:
        return self.storage


async def put_sealed(vault: Vault, conn: AsyncConnection, case_id: uuid.UUID, path: str, data: bytes) -> None:
    sealed = await vault.case_crypto().encrypt(conn, case_id, data, purpose=path)
    await vault.files().put(path, sealed)


async def get_sealed(vault: Vault, conn: AsyncConnection, case_id: uuid.UUID, path: str) -> bytes:
    blob = await vault.files().get(path)
    return await vault.case_crypto().decrypt(conn, case_id, blob, purpose=path)
