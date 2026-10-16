import pytest


def test_worker_entrypoint_needs_the_direct_database_url() -> None:
    from app.core.config import ConfigError
    from app.worker.main import build_worker

    with pytest.raises(ConfigError, match="DATABASE_URL_DIRECT"):
        build_worker({"DATABASE_URL": "postgresql://app_api@db.invalid/postgres"})


def test_worker_entrypoint_runs_the_apps_job_registry_on_the_jobs_queue() -> None:
    from app.core.jobs import registry

    from app.worker.main import build_worker

    worker = build_worker(
        {
            "DATABASE_URL": "postgresql://app_api@db.invalid/postgres",
            "DATABASE_URL_DIRECT": "postgresql://app_worker@db.invalid/postgres",
        }
    )
    assert worker.registry is registry
    assert worker.registry.queue == "jobs"
