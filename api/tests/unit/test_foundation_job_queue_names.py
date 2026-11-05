"""Interactive and bulk work run on separate queues (scalability PR 7), so a rerun or an export that a person
is waiting for never queues behind hours of bundle processing.

- A kind registered `interactive=True` is queued on `interactive`; every other kind on `bulk`.
- A worker takes the queues named in `WORKER_QUEUES`; with none named it takes all, including the old `jobs`
  queue, so work queued before the rollout is still picked up.
- `rerun_checks` and `export_case` are interactive."""

from __future__ import annotations

import pytest

from app.core.config import ConfigError
from app.core.features import load_features
from app.core.jobs import BULK_QUEUE, INTERACTIVE_QUEUE, LEGACY_QUEUE, JobRegistry, registry
from app.worker.main import build_worker

URLS = {
    "DATABASE_URL": "postgresql://app_api:x@db.example:6543/postgres",
    "DATABASE_URL_DIRECT": "postgresql://app_worker:x@db.example:5432/postgres",
}


async def _handler(ctx: object) -> None:
    return None


def test_the_queue_names_are_fixed_words() -> None:
    assert (INTERACTIVE_QUEUE, BULK_QUEUE, LEGACY_QUEUE) == ("interactive", "bulk", "jobs")


def test_an_interactive_kind_goes_to_the_interactive_queue_and_others_to_bulk() -> None:
    reg = JobRegistry(split_queues=True)
    reg.kind("zz_fast", interactive=True)(_handler)
    reg.kind("zz_slow")(_handler)
    assert reg.queue_for("zz_fast") == "interactive"
    assert reg.queue_for("zz_slow") == "bulk"


def test_the_apps_registry_splits_its_queues_and_a_test_registry_keeps_one() -> None:
    assert registry.split_queues is True
    single = JobRegistry(queue="zz_one")
    single.kind("zz_a", interactive=True)(_handler)
    assert single.queue_for("zz_a") == "zz_one"


def test_rerun_checks_and_export_case_are_interactive_and_bulk_work_is_not() -> None:
    load_features()
    assert registry.kinds["rerun_checks"].interactive
    assert registry.kinds["export_case"].interactive
    assert registry.queue_for("rerun_checks") == "interactive"
    assert registry.queue_for("export_case") == "interactive"
    assert registry.queue_for("process_bundle") == "bulk"


def test_a_worker_with_no_queue_list_takes_every_queue_including_the_old_one() -> None:
    worker = build_worker(URLS)
    assert set(worker.queues) == {"interactive", "bulk", "jobs"}


def test_a_worker_takes_the_queues_in_worker_queues() -> None:
    interactive = build_worker({**URLS, "WORKER_QUEUES": "interactive"})
    assert interactive.queues[0] == "interactive" and "bulk" not in interactive.queues
    assert "interactive" not in build_worker({**URLS, "WORKER_QUEUES": " bulk "}).queues
    assert {"interactive", "bulk"} <= set(build_worker({**URLS, "WORKER_QUEUES": "interactive,bulk"}).queues)


def test_the_bulk_worker_drains_the_old_queue_and_the_interactive_worker_does_not() -> None:
    assert "jobs" in build_worker({**URLS, "WORKER_QUEUES": "bulk"}).queues
    assert "jobs" not in build_worker({**URLS, "WORKER_QUEUES": "interactive"}).queues


def test_an_unknown_queue_name_is_refused_by_setting_name() -> None:
    with pytest.raises(ConfigError, match="WORKER_QUEUES"):
        build_worker({**URLS, "WORKER_QUEUES": "interactive,elsewhere"})
