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
