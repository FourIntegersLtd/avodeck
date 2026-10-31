"""One-off: seal the trial files written before sealing (plain PDFs), once per case. Safe to run again.

The files are found through the rows that name them: agendas, prepared statements, statements signed
elsewhere (their path is built from the stored hash), court bundles and trial packs. A file that opens with
the case key is left alone; one that does not open but starts with `%PDF` is sealed and written back. A file
that is missing (a statement signed through the e-sign provider has none) is skipped. The audit row carries
a count only.
"""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection

from app.core import audit
from app.core.crypto import CiphertextInvalid
from app.core.storage import StorageError
from app.features.trial.common import trial_path
from app.features.trial.storage import Vault, put_sealed

PATH_QUERIES = (
    "select storage_path from app.agenda_versions where case_id = %s",
    "select storage_path from app.court_bundles where case_id = %s",
    "select storage_path from app.trial_packs where case_id = %s",
    "select prepared_path from app.expert_meetings where case_id = %s and prepared_path is not null",
)


async def known_paths(conn: AsyncConnection, case_id: uuid.UUID) -> list[str]:
    paths: list[str] = []
    for query in PATH_QUERIES:
        cur = await conn.execute(query, (case_id,))
        paths += [str(r[0]) for r in await cur.fetchall()]
    cur = await conn.execute(
        "select id, signed_sha256 from app.expert_meetings where case_id = %s and signed_sha256 is not null",
        (case_id,),
    )
    paths += [trial_path(case_id, "meetings", r[0], f"signed-{r[1]}.pdf") for r in await cur.fetchall()]
    return list(dict.fromkeys(paths))


async def reseal_plain_files(conn: AsyncConnection, vault: Vault, case_id: uuid.UUID) -> int:
    """Seal every plain trial file of the case; returns how many were sealed."""
    sealed = 0
    for path in await known_paths(conn, case_id):
        try:
            blob = await vault.files().get(path)
        except StorageError:
            continue
        try:
            await vault.case_crypto().decrypt(conn, case_id, blob, purpose=path)
        except CiphertextInvalid:
            if blob.startswith(b"%PDF"):
                await put_sealed(vault, conn, case_id, path, blob)
                sealed += 1
    if sealed:
        await audit.record(conn, action="trial.files_resealed", target=str(sealed), case_id=case_id)
    return sealed
