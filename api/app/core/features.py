"""Finding each slice's package under `app.features` (architecture 8.4).

- `feature_routers(package)`: every feature's `APIRouter`, for `create_app()` to `mount()`. A
  feature exposes it as `app.features.<name>.router.router` (or a `router` attribute on the package).
- `load_features(package)`: imports every feature package and its `jobs` and `router` modules, so
  job kinds and schedules registered there are known (the worker calls this).

- `start_features(package, target, hook=...)`: calls each feature's start-up hook, in name order.
  The API calls `on_startup(app)` once its database is ready; the worker calls
  `on_worker_start(worker)` once its database is open. A hook may be sync or async; a feature
  without one is skipped; a failing hook stops the start.

Features load in name order. A feature without a router or jobs module is fine.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import pkgutil
from types import ModuleType
from typing import Any

from fastapi import APIRouter

DEFAULT_PACKAGE = "app.features"


def feature_packages(package: str = DEFAULT_PACKAGE) -> list[ModuleType]:
    root = importlib.import_module(package)
    names = sorted(m.name for m in pkgutil.iter_modules(root.__path__) if m.ispkg)
    return [importlib.import_module(f"{package}.{name}") for name in names]


def _submodule(feature: ModuleType, name: str) -> ModuleType | None:
    full = f"{feature.__name__}.{name}"
    if importlib.util.find_spec(full) is None:
        return None
    return importlib.import_module(full)


def feature_routers(package: str = DEFAULT_PACKAGE) -> list[APIRouter]:
    routers: list[APIRouter] = []
    for feature in feature_packages(package):
        found = getattr(feature, "router", None)
        if not isinstance(found, APIRouter):
            module = _submodule(feature, "router")
            found = getattr(module, "router", None) if module is not None else None
        if isinstance(found, APIRouter):
            routers.append(found)
    return routers


def load_features(package: str = DEFAULT_PACKAGE) -> list[ModuleType]:
    features = feature_packages(package)
    for feature in features:
        for name in ("jobs", "router"):
            _submodule(feature, name)
    return features


API_HOOK = "on_startup"
WORKER_HOOK = "on_worker_start"


async def start_features(package: str, target: Any, *, hook: str = API_HOOK) -> None:
    """Call `hook(target)` on every feature package that defines it, in name order."""
    for feature in feature_packages(package):
        found = getattr(feature, hook, None)
        if callable(found):
            result = found(target)
            if inspect.isawaitable(result):
                await result
