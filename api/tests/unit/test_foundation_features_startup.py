"""Feature start-up hooks (`app.core.features`): a slice can run work when the API or the worker starts.

- A feature package may define `on_startup(app)` (the FastAPI app, once its database is ready) and
  `on_worker_start(worker)` (the worker, once its database is open). Either may be sync or async.
- Hooks run in feature name order; a feature without one is skipped. A failing hook stops the start.
Slice D uses this to bring the shared guideline versions table up to date with the packs.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core import features as core_features
from app.main import create_app

pytestmark = pytest.mark.anyio

HOOKS = """
CALLS = []


async def on_startup(app):
    CALLS.append(("api", app.state.database))


def on_worker_start(worker):
    CALLS.append(("worker", worker))
"""

SYNC_ONLY = """
CALLS = []


def on_startup(app):
    CALLS.append(("api", app.state.region))
"""

BROKEN = """
def on_startup(app):
    raise RuntimeError("zz broken start")
"""


def features_package(root: Path, members: dict[str, str]) -> str:
    name = f"zz_startup_{uuid.uuid4().hex[:10]}"
    package = root / name
    package.mkdir()
    (package / "__init__.py").write_text("")
    for member, code in members.items():
        (package / member).mkdir()
        (package / member / "__init__.py").write_text(code)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return name


def module(package: str, member: str) -> Any:
    return sys.modules[f"{package}.{member}"]


def an_app(package: str) -> Any:
    auth = SimpleNamespace(
        settings=SimpleNamespace(
            app_origin="https://zz.invalid", app_origins=("https://zz.invalid",), cookie_domain=None
        )
    )
    return create_app(
        database=SimpleNamespace(name="zz-db"),  # type: ignore[arg-type]
        region="zz",
        auth=auth,  # type: ignore[arg-type]
        storage=SimpleNamespace(),  # type: ignore[arg-type]
        crypto=SimpleNamespace(),  # type: ignore[arg-type]
        features=package,
    )


async def test_the_api_runs_each_features_startup_hook_once_its_database_is_ready(tmp_path: Path) -> None:
    package = features_package(tmp_path, {"b_sync": SYNC_ONLY, "a_hooks": HOOKS, "c_quiet": ""})
    app = an_app(package)
    assert module(package, "a_hooks").CALLS == []
    async with app.router.lifespan_context(app):
        assert module(package, "a_hooks").CALLS == [("api", app.state.database)]
        assert module(package, "b_sync").CALLS == [("api", "zz")]


async def test_a_failing_startup_hook_stops_the_app_starting(tmp_path: Path) -> None:
    app = an_app(features_package(tmp_path, {"broken": BROKEN}))
    with pytest.raises(RuntimeError, match="zz broken start"):
        async with app.router.lifespan_context(app):
            pass  # pragma: no cover - never reached


async def test_start_features_runs_the_named_hook_in_feature_name_order(tmp_path: Path) -> None:
    order: list[str] = []
    code = "def on_worker_start(worker):\n    worker.append(__name__.rsplit('.', 1)[-1])\n"
    package = features_package(tmp_path, {"zz_second": code, "zz_first": code, "zz_none": ""})
    await core_features.start_features(package, order, hook="on_worker_start")
    assert order == ["zz_first", "zz_second"]


async def test_the_worker_runs_each_features_worker_hook_after_opening_its_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.worker import main as worker_main

    package = features_package(tmp_path, {"hooks": HOOKS})
    events: list[str] = []

    class FakeDatabase:
        async def open(self) -> None:
            events.append("open")

        async def close(self) -> None:
            events.append("close")

    class FakeWorker:
        database = FakeDatabase()

        async def __aenter__(self) -> FakeWorker:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def run(self, **kwargs: object) -> None:
            events.append(f"run:{len(module(package, 'hooks').CALLS)}")

    fake = FakeWorker()
    monkeypatch.setattr(worker_main, "build_worker", lambda env, features=package: fake)
    await worker_main.run({}, features=package)
    assert module(package, "hooks").CALLS == [("worker", fake)]
    assert events == ["open", "run:1", "close"]
