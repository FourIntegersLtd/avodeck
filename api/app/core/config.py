"""Cell configuration read from the environment.

Nothing region-specific is written in code: each cell (REGION=uk first) supplies its own
values. This module grows with the foundation steps (endpoints, model IDs, allowed packs).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from urllib.parse import urlsplit


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


# --- AI cell: in-region models and endpoints (rule 5, architecture 2.3 and 6.1) ---------------------------------------

MODEL_ROLES = {"sonnet": "BEDROCK_SONNET_MODEL_ID", "opus": "BEDROCK_OPUS_MODEL_ID"}
ENDPOINTS = ("BEDROCK_ENDPOINT", "AZURE_DI_ENDPOINT", "AZURE_SPEECH_ENDPOINT")
# A bare in-region foundation-model ID: provider, dot, model. Cross-region inference profiles
# (`global.`, `eu.`, `us.`, `apac.` ...) and ARNs (which can name any region) are refused.
_IN_REGION_MODEL = re.compile(r"^anthropic\.[a-z0-9][a-z0-9.\-]*(:\d+)?$")


@dataclass(frozen=True)
class AiSettings:
    """The cell's AI services. Every value comes from the cell config; nothing is written in code.

    `allowed_hosts` is the cell's allow-list: no AI client may talk to any other host. The cost
    guard (F11, K3) pauses a case once its tokens pass `cost_guard_multiple` x the expected use,
    `expected_tokens_per_page` x its pages (at least `min_expected_pages`).
    """

    region: str
    bedrock_region: str
    models: Mapping[str, str]
    bedrock_endpoint: str
    ocr_endpoint: str
    speech_endpoint: str
    allowed_hosts: frozenset[str]
    expected_tokens_per_page: int = 4000
    min_expected_pages: int = 100
    cost_guard_multiple: int = 3


def in_region_model_id(model_id: str) -> bool:
    return bool(_IN_REGION_MODEL.match(model_id))


def endpoint_allowed(url: str, allowed_hosts: frozenset[str]) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("https", "wss") and parts.hostname is not None and parts.hostname in allowed_hosts


def ai_settings(env: Mapping[str, str] | None = None) -> AiSettings:
    source = os.environ if env is None else env
    names = ("REGION", "AWS_REGION", *MODEL_ROLES.values(), *ENDPOINTS, "AI_ENDPOINT_ALLOW_LIST")
    values = {name: source.get(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError(f"{', '.join(missing)} not set")
    for name in MODEL_ROLES.values():
        if not in_region_model_id(values[name]):
            raise ConfigError(f"{name} is not an in-region model ID (no cross-region profiles or ARNs)")
    hosts = frozenset(h.strip().lower() for h in values["AI_ENDPOINT_ALLOW_LIST"].split(",") if h.strip())
    for name in ENDPOINTS:
        if not endpoint_allowed(values[name], hosts):
            raise ConfigError(f"{name} is not a secure endpoint on AI_ENDPOINT_ALLOW_LIST")

    def number(name: str, default: int) -> int:
        raw = source.get(name, "").strip()
        try:
            value = int(raw) if raw else default
        except ValueError as exc:
            raise ConfigError(f"{name} must be a whole number") from exc
        if value < 1:
            raise ConfigError(f"{name} must be at least 1")
        return value

    return AiSettings(
        region=values["REGION"],
        bedrock_region=values["AWS_REGION"],
        models=MappingProxyType({role: values[name] for role, name in MODEL_ROLES.items()}),
        bedrock_endpoint=values["BEDROCK_ENDPOINT"],
        ocr_endpoint=values["AZURE_DI_ENDPOINT"],
        speech_endpoint=values["AZURE_SPEECH_ENDPOINT"],
        allowed_hosts=hosts,
        expected_tokens_per_page=number("AI_EXPECTED_TOKENS_PER_PAGE", 4000),
        min_expected_pages=number("AI_MIN_EXPECTED_PAGES", 100),
        cost_guard_multiple=number("AI_COST_GUARD_MULTIPLE", 3),
    )
