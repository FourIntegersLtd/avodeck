"""Rule 7 for slice I: the subject's name, other name and date of birth never reach T-22a or T-22b.

The agenda types the name, the other name and the date of birth in three forms; the case has a recorded
subject. What leaves the cell holds none of them, and the agenda's own words still go.
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.fixtures.trial are used as arguments
from __future__ import annotations

from collections.abc import Callable

import pytest

from app.core.db import Database
from tests.fixtures.trial.fixtures import (  # noqa: F401
    Scripted,
    aconn,
    app,
    changes,
    model,
    reads,
    storage,
    worker_db,
    world,
)
from tests.fixtures.trial.world import CRYPTO, World
from tests.http.test_trial_meetings import AGENDA, agenda, create
from tests.support.db import ClientFor
from tests.support.identity import leaked, record_identity
from tests.support.packs import LAUNCH, Combo

pytestmark = pytest.mark.anyio


@pytest.fixture
def combo() -> Combo:
    return LAUNCH


async def test_t22_never_carries_the_subject(
    client_for: ClientFor, world: World, worker_db: Database, model: Callable[..., Scripted], aconn: object
) -> None:
    scripted = model()
    await record_identity(aconn, CRYPTO, world.case)  # type: ignore[arg-type]
    created = await create(client_for, world)

    await agenda(
        client_for,
        worker_db,
        world,
        created["id"],
        [
            *AGENDA,
            f"{AGENDA[0]} Jane Testperson also Jane Oldname born 01/02/1960, again 1 February 1960 or 01 Feb 1960?",
        ],
    )

    sent = [r.text for r in scripted.requests if r.task_id in ("T-22a", "T-22b")]
    assert sent and any("low iron" in t for t in sent)
    assert leaked(sent) == []
