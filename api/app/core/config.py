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


def env_int(source: Mapping[str, str], name: str, default: int, *, minimum: int | None = None) -> int:
    """A whole-number setting; unset or empty gives `default`. Anything else is a ConfigError naming it."""
    raw = source.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")
    return value


_TRUE_WORDS = frozenset({"1", "true", "yes"})
_FALSE_WORDS = frozenset({"", "0", "false", "no"})


def env_bool(source: Mapping[str, str], name: str) -> bool:
    """An on/off setting (1/true/yes or 0/false/no/unset); any other word is a ConfigError, never a silent off."""
    raw = source.get(name, "").strip().lower()
    if raw in _TRUE_WORDS:
        return True
    if raw in _FALSE_WORDS:
        return False
    raise ConfigError(f"{name} must be 1 or 0")


def env_csv(source: Mapping[str, str], name: str) -> tuple[str, ...]:
    """A comma-separated list setting, trimmed, empties dropped."""
    return tuple(part.strip() for part in source.get(name, "").split(",") if part.strip())


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

    `app_origins` is every origin allowed to send this API a request with cookies: `APP_ORIGIN`, one
    or several separated by commas. The API lives on its own origin (ADR 0031), so each of those is
    cross-origin and needs CORS; a wildcard is refused, since a browser ignores one on a credentialed
    request anyway. `app_origin` is the first of them, the one the API builds links in email from.

    `cookie_domain` widens the CSRF cookie so page script on the web app's origin can read it when the
    API is on a different host (`COOKIE_DOMAIN`, e.g. `avodeck.com`). It must cover every allowed
    origin, so a cell cannot scope its cookie to a domain it does not serve. Unset (same-origin
    hosting and local development) leaves every cookie host-only.
    """

    url: str
    anon_key: str = field(repr=False)
    service_role_key: str = field(repr=False)
    app_origins: tuple[str, ...]
    cookie_domain: str | None = None

    @property
    def app_origin(self) -> str:
        return self.app_origins[0]


def _app_origins(value: str) -> tuple[str, ...]:
    origins = tuple(part.strip().rstrip("/").lower() for part in value.split(",") if part.strip())
    if not origins:
        raise ConfigError("APP_ORIGIN is not set")
    for origin in origins:
        if "*" in origin:
            raise ConfigError(f"APP_ORIGIN must name each origin in full, not a pattern: {origin!r}")
        parts = urlsplit(origin)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.path:
            raise ConfigError(f"APP_ORIGIN is not an origin: {origin!r}")
    return origins


def _cookie_domain(value: str, origins: tuple[str, ...]) -> str | None:
    domain = value.strip().lstrip(".").rstrip(".").lower()
    if not domain:
        return None
    if "." not in domain:
        raise ConfigError(f"COOKIE_DOMAIN must be a domain name: {domain!r}")
    for origin in origins:
        host = (urlsplit(origin).hostname or "").lower()
        if host != domain and not host.endswith(f".{domain}"):
            raise ConfigError(f"COOKIE_DOMAIN {domain!r} does not cover APP_ORIGIN {origin!r}")
    return domain


def auth_settings(env: Mapping[str, str] | None = None) -> AuthSettings:
    source = os.environ if env is None else env
    names = ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY", "APP_ORIGIN")
    values = {name: source.get(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError(f"{', '.join(missing)} not set")
    origins = _app_origins(values["APP_ORIGIN"])
    return AuthSettings(
        url=values["SUPABASE_URL"].rstrip("/"),
        anon_key=values["SUPABASE_ANON_KEY"],
        service_role_key=values["SUPABASE_SERVICE_ROLE_KEY"],
        app_origins=origins,
        cookie_domain=_cookie_domain(source.get("COOKIE_DOMAIN", ""), origins),
    )


@dataclass(frozen=True)
class WebhookSettings:
    """Secrets for inbound provider webhooks (the e-sign provider first). Hidden from repr.

    `None` when the cell has none set: the webhook then accepts nothing.
    """

    esign_secret: str | None = field(default=None, repr=False)


def webhook_settings(env: Mapping[str, str] | None = None) -> WebhookSettings:
    source = os.environ if env is None else env
    return WebhookSettings(esign_secret=source.get("ESIGN_WEBHOOK_SECRET", "").strip() or None)


_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def local_mfa_skip(env: Mapping[str, str] | None = None) -> bool:
    """`DEV_SKIP_MFA=1`: local development only, a password sign-in counts as a completed code
    (rule 10 off). Refused unless Auth is a local Supabase, so no deployed cell can turn it on."""
    source = os.environ if env is None else env
    if not env_bool(source, "DEV_SKIP_MFA"):
        return False
    if urlsplit(source.get("SUPABASE_URL", "").strip()).hostname not in _LOCAL_HOSTS:
        raise ConfigError("DEV_SKIP_MFA is only allowed against a local Supabase")
    return True


# --- AI cell: in-region models and endpoints (rule 5, architecture 2.3 and 6.1) ---------------------------------------

MODEL_ROLES = {"sonnet": "BEDROCK_SONNET_MODEL_ID", "opus": "BEDROCK_OPUS_MODEL_ID"}
ENDPOINTS = ("BEDROCK_ENDPOINT", "AZURE_DI_ENDPOINT", "AZURE_SPEECH_ENDPOINT")
# A bare in-region foundation-model ID: provider, dot, model. Cross-region inference profiles
# (`global.`, `eu.`, `us.`, `apac.` ...) and ARNs (which can name any region) are refused.
_IN_REGION_MODEL = re.compile(r"^anthropic\.[a-z0-9][a-z0-9.\-]*(:\d+)?$")


# The direct Anthropic API as the primary model provider. Sets rule 5 aside knowingly, behind
# `AI_PRIMARY_PROVIDER=anthropic` only (ADR 0036). Everything below applies only then.
PROVIDERS = ("bedrock", "anthropic")
ANTHROPIC_DIRECT_MODELS = {
    "sonnet": "claude-sonnet-4-6"
}  # temporary, dummy data only (ADR 0036); no opus: it goes to Bedrock
ANTHROPIC_ENDPOINT = "https://api.anthropic.com"  # temporary, dummy data only (ADR 0036)
ANTHROPIC_HOST = "api.anthropic.com"  # temporary, dummy data only (ADR 0036): added to the allow-list in code


@dataclass(frozen=True)
class ProviderLimits:
    """One model provider's own ceilings. Providers never share a pool (ADR 0036)."""

    rpm: int  # requests per minute (the slot scheduler's ceiling for this provider)
    pages_per_call: int  # pages the pipeline puts in one call
    max_output_tokens: int  # the largest answer a call to this provider may ask for
    concurrency: int  # calls in flight at once
    widest_output_tokens: int = 0  # the wider answer a page cut off alone may ask for once; 0 = no wider ask


# temporary, dummy data only (ADR 0036): many small calls in parallel, not a few big ones
# A dense page's answer was cut off at 8000 tokens (the JSON ends mid-way and fails its schema). The direct
# client always streams (`anthropic_live`), so the SDK's large-`max_tokens` refusal does not apply: the cap is
# 32000, and a page cut off even on its own is asked once more with 64000 (the model's own ceiling).
# The API reserves each call's max_tokens against the per-minute output allowance when it admits the call, so
# concurrency x cap (50 x 32000 = 1.6M) is kept at the old 200 x 8000.
ANTHROPIC_LIMITS = ProviderLimits(
    rpm=1000, pages_per_call=5, max_output_tokens=32000, concurrency=50, widest_output_tokens=64000
)
BEDROCK_MAX_OUTPUT_TOKENS = 16000
BEDROCK_CONCURRENCY = 3


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
    bedrock_rpm_limit: int = 5
    extraction_batch_size: int = 10
    # Temporary, dummy data only (ADR 0036). The defaults are today's behaviour: Bedrock only.
    primary_provider: str = "bedrock"
    direct_models: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    anthropic_endpoint: str = ""

    @property
    def limits(self) -> Mapping[str, ProviderLimits]:
        """Each provider's own limits. `anthropic` is present only while it is the primary (ADR 0036)."""
        found = {
            "bedrock": ProviderLimits(
                rpm=self.bedrock_rpm_limit,
                pages_per_call=self.extraction_batch_size,
                max_output_tokens=BEDROCK_MAX_OUTPUT_TOKENS,
                concurrency=BEDROCK_CONCURRENCY,
            )
        }
        if self.primary_provider == "anthropic":
            found["anthropic"] = ANTHROPIC_LIMITS
        return MappingProxyType(found)

    @property
    def primary_limits(self) -> ProviderLimits:
        return self.limits[self.primary_provider]

    # In-region models tried, in order, when the primary model is throttled (each has its own quota bucket).
    overflow_models: tuple[str, ...] = ()


def in_region_model_id(model_id: str) -> bool:
    return bool(_IN_REGION_MODEL.match(model_id))


def endpoint_allowed(url: str, allowed_hosts: frozenset[str]) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("https", "wss") and parts.hostname is not None and parts.hostname in allowed_hosts


def primary_provider(env: Mapping[str, str] | None = None) -> str:
    """`AI_PRIMARY_PROVIDER`: `bedrock` (unset, the default) or `anthropic` (needs `ANTHROPIC_API_KEY`; ADR 0036).

    Only the key's presence is read here; its value is never stored in the settings."""
    source = os.environ if env is None else env
    provider = source.get("AI_PRIMARY_PROVIDER", "").strip() or "bedrock"
    if provider not in PROVIDERS:
        raise ConfigError("AI_PRIMARY_PROVIDER must be bedrock or anthropic")
    if provider == "anthropic" and not source.get("ANTHROPIC_API_KEY", "").strip():
        raise ConfigError("AI_PRIMARY_PROVIDER=anthropic needs ANTHROPIC_API_KEY")
    return provider


@dataclass(frozen=True)
class CostGuard:
    """The cost guard's allowance (F11, K3): a case may use `multiple` x `expected_tokens_per_page` x the larger of
    its pages and `min_expected_pages` tokens. The same numbers the AI kit checks, readable without the cell's
    AI endpoints (e.g. by a route that only needs to know whether a case is over its allowance)."""

    multiple: int = 3
    expected_tokens_per_page: int = 4000
    min_expected_pages: int = 100

    def allowance(self, pages: int) -> int:
        return self.multiple * self.expected_tokens_per_page * max(pages, self.min_expected_pages)


def cost_guard(env: Mapping[str, str] | None = None) -> CostGuard:
    source = os.environ if env is None else env
    defaults = CostGuard()
    return CostGuard(
        multiple=env_int(source, "AI_COST_GUARD_MULTIPLE", defaults.multiple, minimum=1),
        expected_tokens_per_page=env_int(
            source, "AI_EXPECTED_TOKENS_PER_PAGE", defaults.expected_tokens_per_page, minimum=1
        ),
        min_expected_pages=env_int(source, "AI_MIN_EXPECTED_PAGES", defaults.min_expected_pages, minimum=1),
    )


def ai_settings(env: Mapping[str, str] | None = None) -> AiSettings:
    source = os.environ if env is None else env
    provider = primary_provider(source)
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

    listed = [m.strip() for m in source.get("BEDROCK_OVERFLOW_MODEL_IDS", "").split(",") if m.strip()]
    if not all(in_region_model_id(m) for m in listed):
        raise ConfigError(
            "BEDROCK_OVERFLOW_MODEL_IDS has an ID that is not an in-region model ID (no cross-region profiles or ARNs)"
        )

    primaries = {values[name] for name in MODEL_ROLES.values()}
    overflow = tuple(dict.fromkeys(m for m in listed if m not in primaries))

    direct = provider == "anthropic"
    return AiSettings(
        region=values["REGION"],
        bedrock_region=values["AWS_REGION"],
        models=MappingProxyType({role: values[name] for role, name in MODEL_ROLES.items()}),
        bedrock_endpoint=values["BEDROCK_ENDPOINT"],
        ocr_endpoint=values["AZURE_DI_ENDPOINT"],
        speech_endpoint=values["AZURE_SPEECH_ENDPOINT"],
        allowed_hosts=hosts | {ANTHROPIC_HOST} if direct else hosts,
        expected_tokens_per_page=number("AI_EXPECTED_TOKENS_PER_PAGE", 4000),
        min_expected_pages=number("AI_MIN_EXPECTED_PAGES", 100),
        cost_guard_multiple=number("AI_COST_GUARD_MULTIPLE", 3),
        bedrock_rpm_limit=number("BEDROCK_RPM_LIMIT", 5),
        extraction_batch_size=number("AI_EXTRACTION_BATCH_SIZE", 10),
        primary_provider=provider,
        direct_models=MappingProxyType(dict(ANTHROPIC_DIRECT_MODELS)) if direct else MappingProxyType({}),
        anthropic_endpoint=ANTHROPIC_ENDPOINT if direct else "",
        overflow_models=overflow,
    )


@dataclass(frozen=True)
class CellPublic:
    """What the sign-in screen may show about this cell before anyone signs in. A blank setting is null."""

    storage_place: str | None


def cell_public(env: Mapping[str, str] | None = None) -> CellPublic:
    source = os.environ if env is None else env

    def read(name: str) -> str | None:
        return source.get(name, "").strip() or None

    return CellPublic(read("STORAGE_PLACE"))


SUPPORT_EMAIL = "hello@avodeck.com"


def support_email() -> str:
    """Our own support address, where Help form messages go. Never sent to the browser."""
    return SUPPORT_EMAIL


@dataclass(frozen=True)
class ClamdSettings:
    """The virus scanner (clamd) on the private network, found only through the cell config."""

    host: str
    port: int = 3310
    timeout_s: float = 60.0


def clamd_settings(env: Mapping[str, str] | None = None) -> ClamdSettings:
    source = os.environ if env is None else env
    host = source.get("CLAMD_HOST", "").strip()
    if not host:
        raise ConfigError("CLAMD_HOST is not set")
    try:
        port = env_int(source, "CLAMD_PORT", 3310, minimum=1)
        timeout_s = float(source.get("CLAMD_TIMEOUT_S", "").strip() or 60)
    except ValueError as exc:
        raise ConfigError("CLAMD_PORT and CLAMD_TIMEOUT_S must be numbers") from exc
    return ClamdSettings(host=host, port=port, timeout_s=timeout_s)
