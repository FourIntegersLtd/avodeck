"""Cell configuration read from the environment.

Nothing region-specific is written in code: each cell (REGION=uk first) supplies its own
values. This module grows with the foundation steps (endpoints, model IDs, allowed packs).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field


class ConfigError(RuntimeError):
    """A required setting is missing or invalid."""


@dataclass(frozen=True)
class DatabaseSettings:
    """Connection strings for the restricted roles (ADR 0013). Hidden from repr so they never reach logs.

    `url` is `app_api` through the pooler (transaction mode); `url_direct` is `app_worker` on a
    direct connection, needed by the job queue. The migrator's URL is deliberately not read here.
    """

    url: str = field(repr=False)
    url_direct: str | None = field(default=None, repr=False)


def database_settings(env: Mapping[str, str] | None = None) -> DatabaseSettings:
    source = os.environ if env is None else env
    url = source.get("DATABASE_URL", "").strip()
    if not url:
        raise ConfigError("DATABASE_URL is not set")
    direct = source.get("DATABASE_URL_DIRECT", "").strip() or None
    return DatabaseSettings(url=url, url_direct=direct)


@dataclass(frozen=True)
class CellSettings:
    """The cell this process serves (rule 5): `REGION=uk` first. Stamped on new people and organisations."""

    region: str


def cell_settings(env: Mapping[str, str] | None = None) -> CellSettings:
    source = os.environ if env is None else env
    region = source.get("REGION", "").strip()
    if not region:
        raise ConfigError("REGION is not set")
    return CellSettings(region=region)


@dataclass(frozen=True)
class AuthSettings:
    """Supabase Auth for this cell, called only by FastAPI (rule 4). Keys hidden from repr.

    The service-role key is used only for Auth admin calls (ADR 0013), never for the database.
    `app_origin` is the web app's origin: the only one allowed to send requests with cookies.
    """

    url: str
    anon_key: str = field(repr=False)
    service_role_key: str = field(repr=False)
    app_origin: str


def auth_settings(env: Mapping[str, str] | None = None) -> AuthSettings:
    source = os.environ if env is None else env
    names = ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY", "APP_ORIGIN")
    values = {name: source.get(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError(f"{', '.join(missing)} not set")
    return AuthSettings(
        url=values["SUPABASE_URL"].rstrip("/"),
        anon_key=values["SUPABASE_ANON_KEY"],
        service_role_key=values["SUPABASE_SERVICE_ROLE_KEY"],
        app_origin=values["APP_ORIGIN"].rstrip("/"),
    )
