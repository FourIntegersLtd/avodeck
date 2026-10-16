import pytest


def test_worker_entrypoint_runs_from_the_api_package_and_exits_with_no_handlers() -> None:
    from app.worker.main import main

    with pytest.raises(SystemExit, match="no job handlers"):
        main()
