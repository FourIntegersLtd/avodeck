"""Finding each slice's package under `app.features` (architecture 8.4).

- `feature_routers(package)`: every feature's `APIRouter`, for `create_app()` to `mount()`. A
  feature exposes it as `app.features.<name>.router.router` (or a `router` attribute on the package).
- `load_features(package)`: imports every feature package and its `jobs` and `router` modules, so
  job kinds and schedules registered there are known (the worker calls this).

Features load in name order. A feature without a router or jobs module is fine.
"""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
from types import ModuleType

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
