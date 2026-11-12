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

from urllib3.exceptions import LocationParseError
from urllib3.util import parse_url


class ConfigError(RuntimeError):
    """A required setting is missing or invalid."""


def _whole(raw: str) -> int | None:
    """`raw` as a whole number, or None. Parsed here so a failure never chains the raw value into a traceback."""
    try:
        return int(raw)
    except ValueError:
        return None


def _decimal(raw: str) -> float | None:
    try:
        return float(raw)
    except ValueError:
        return None


def env_int(source: Mapping[str, str], name: str, default: int, *, minimum: int | None = None) -> int:
    """A whole-number setting; unset or empty gives `default`. Anything else is a ConfigError naming it."""
    raw = source.get(name, "").strip()
    if not raw:
        return default
    value = _whole(raw)
    if value is None:
        raise ConfigError(f"{name} must be a whole number")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")
    return value


def env_float(source: Mapping[str, str], name: str, default: float, *, minimum: float | None = None) -> float:
    """A number setting; unset or empty gives `default`. Anything else is a ConfigError naming it."""
    raw = source.get(name, "").strip()
    if not raw:
        return default
    value = _decimal(raw)
    if value is None:
        raise ConfigError(f"{name} must be a number")
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


# The worker's connections: its own pool and the job queue library's. A hosted session pooler lets only so
# many clients in (Supabase: 15 by default), and the two together must stay under that number.
DEFAULT_WORKER_POOL_MAX = 10
DEFAULT_QUEUE_POOL_MAX = 4
# The API's pool fails fast (PR 1 of docs/ops/scalability-plan.md): a request waits this long for a connection,
# at most this many wait at once, and a statement runs at most this long (0 = no limit). The worker has none.
DEFAULT_API_POOL_MAX = 10  # per API process; the API runs several (WEB_CONCURRENCY) on several replicas (PR 16)
DEFAULT_API_ACQUIRE_TIMEOUT_SECONDS = 2.0
DEFAULT_API_MAX_WAITING = 50
DEFAULT_API_STATEMENT_TIMEOUT_MS = 5000


@dataclass(frozen=True)
class DatabaseSettings:
    """Connection strings for the restricted roles (ADR 0013). Hidden from repr so they never reach logs.

    `url` is `app_api` through the pooler (transaction mode); `url_direct` is `app_worker` on a
    direct connection, needed by the job queue. The migrator's URL is deliberately not read here.
    """

    url: str = field(repr=False)
    url_direct: str | None = field(default=None, repr=False)
    worker_pool_max: int = DEFAULT_WORKER_POOL_MAX
    queue_pool_max: int = DEFAULT_QUEUE_POOL_MAX
    api_pool_max: int = DEFAULT_API_POOL_MAX
    acquire_timeout_seconds: float = DEFAULT_API_ACQUIRE_TIMEOUT_SECONDS
    max_waiting: int = DEFAULT_API_MAX_WAITING
    statement_timeout_ms: int = DEFAULT_API_STATEMENT_TIMEOUT_MS


TRANSACTION_MODE_PORT = "6543"  # Supabase's pooler in transaction mode: it cannot LISTEN


def listen_url(env: Mapping[str, str] | None = None) -> str | None:
    """`DATABASE_URL_LISTEN`, or None when unset (streams then keep their timed loop).

    It is `app_api` on a direct or session-mode connection (port 5432), never transaction mode, which cannot
    LISTEN: a URL on the transaction-mode port is refused at start-up rather than failing silently later (PR 8).
    """
    source = os.environ if env is None else env
    url = source.get("DATABASE_URL_LISTEN", "").strip()
    if not url:
        return None
    if urlsplit(url).port == int(TRANSACTION_MODE_PORT):
        raise ConfigError(
            "DATABASE_URL_LISTEN is on the transaction-mode port (6543), which cannot LISTEN;"
            " use the direct or session-mode URL (port 5432)"
        )
    return url


def database_settings(env: Mapping[str, str] | None = None) -> DatabaseSettings:
    source = os.environ if env is None else env
    url = source.get("DATABASE_URL", "").strip()
    if not url:
        raise ConfigError("DATABASE_URL is not set")
    direct = source.get("DATABASE_URL_DIRECT", "").strip() or None
    return DatabaseSettings(
        url=url,
        url_direct=direct,
        worker_pool_max=env_int(source, "WORKER_DB_POOL_MAX", DEFAULT_WORKER_POOL_MAX, minimum=1),
        queue_pool_max=env_int(source, "WORKER_QUEUE_POOL_MAX", DEFAULT_QUEUE_POOL_MAX, minimum=1),
        api_pool_max=env_int(source, "API_DB_POOL_MAX", DEFAULT_API_POOL_MAX, minimum=1),
        acquire_timeout_seconds=env_float(
            source, "API_DB_ACQUIRE_TIMEOUT_SECONDS", DEFAULT_API_ACQUIRE_TIMEOUT_SECONDS, minimum=0.1
        ),
        max_waiting=env_int(source, "API_DB_MAX_WAITING", DEFAULT_API_MAX_WAITING, minimum=0),
        statement_timeout_ms=env_int(
            source, "API_DB_STATEMENT_TIMEOUT_MS", DEFAULT_API_STATEMENT_TIMEOUT_MS, minimum=0
        ),
    )


# A job put back after its worker stalled is not a failure (F6), but one that stalls every run must end: after
# this many stall requeues the next stall fails the job (`stalled_too_often`). 0 never puts a stalled job back.
DEFAULT_JOB_MAX_STALL_REQUEUES = 5


def job_max_stall_requeues(env: Mapping[str, str] | None = None) -> int:
    """`JOB_MAX_STALL_REQUEUES`, default 5."""
    source = os.environ if env is None else env
    return env_int(source, "JOB_MAX_STALL_REQUEUES", DEFAULT_JOB_MAX_STALL_REQUEUES, minimum=0)


# BG-12: the longest one run of a job may take. It is a backstop for a HUNG job, not a deadline for a slow one: an
# export, a trial pack, a deletion or a bundle of 4,000 pages can legitimately run for hours, and a run that is stopped
# is tried again. A kind may set its own at registration (`registry.kind(..., time_limit_seconds=...)`).
DEFAULT_JOB_TIME_LIMIT_BULK_SECONDS = 6 * 60 * 60
DEFAULT_JOB_TIME_LIMIT_INTERACTIVE_SECONDS = 60 * 60
# BG-14: finished queue rows (and their events) older than this many days are deleted.
DEFAULT_QUEUE_RETENTION_DAYS = 30


@dataclass(frozen=True)
class JobTimeLimits:
    """Seconds one run of a job may take: `bulk` and `interactive` kinds. A run past it is stopped and goes through
    the ordinary failure path (retry, then `failed` with `timed_out`)."""

    bulk: float = DEFAULT_JOB_TIME_LIMIT_BULK_SECONDS
    interactive: float = DEFAULT_JOB_TIME_LIMIT_INTERACTIVE_SECONDS


def job_time_limits(env: Mapping[str, str] | None = None) -> JobTimeLimits:
    """`JOB_TIME_LIMIT_BULK_SECONDS` (default 21600, 6 hours), `JOB_TIME_LIMIT_INTERACTIVE_SECONDS` (default 3600)."""
    source = os.environ if env is None else env
    return JobTimeLimits(
        bulk=env_float(source, "JOB_TIME_LIMIT_BULK_SECONDS", DEFAULT_JOB_TIME_LIMIT_BULK_SECONDS, minimum=1),
        interactive=env_float(
            source, "JOB_TIME_LIMIT_INTERACTIVE_SECONDS", DEFAULT_JOB_TIME_LIMIT_INTERACTIVE_SECONDS, minimum=1
        ),
    )


def queue_retention_days(env: Mapping[str, str] | None = None) -> int:
    """`QUEUE_RETENTION_DAYS`, default 30: how long finished queue rows are kept."""
    source = os.environ if env is None else env
    return env_int(source, "QUEUE_RETENTION_DAYS", DEFAULT_QUEUE_RETENTION_DAYS, minimum=1)


DEFAULT_PIPELINE_BATCH_WINDOW = 2


@dataclass(frozen=True)
class PipelineBatchSettings:
    """Page-batch jobs (ADR 0038). `pages` is the pages in one batch; 0 keeps today's single `process_bundle` job.
    `window` is how many batches of one run read at once (at most `WORKER_CONCURRENCY - 1`, so the parent and a
    waiting job always have a slot). `min_pages` is the fewest pages that make a bundle worth batching."""

    pages: int = 0
    window: int = DEFAULT_PIPELINE_BATCH_WINDOW
    min_pages: int = 0

    @property
    def enabled(self) -> bool:
        return self.pages > 0


def pipeline_batch_settings(env: Mapping[str, str] | None = None) -> PipelineBatchSettings:
    """`PIPELINE_BATCH_PAGES` (default 0: off), `PIPELINE_BATCH_WINDOW` (default 2), `PIPELINE_BATCH_MIN_PAGES`
    (default: the pages setting)."""
    source = os.environ if env is None else env
    pages = env_int(source, "PIPELINE_BATCH_PAGES", 0, minimum=0)
    return PipelineBatchSettings(
        pages=pages,
        window=env_int(source, "PIPELINE_BATCH_WINDOW", DEFAULT_PIPELINE_BATCH_WINDOW, minimum=1),
        min_pages=env_int(source, "PIPELINE_BATCH_MIN_PAGES", pages, minimum=0),
    )


@dataclass(frozen=True)
class DocumentLimits:
    """What one document may cost to process (audit 13). Page caps: `MAX_DOCUMENT_PAGES` per document,
    `MAX_BUNDLE_PAGES` per bundle (4,000, non-functional requirement C1). Images: `MAX_IMAGE_PIXELS` per frame,
    `MAX_IMAGE_FRAMES` per file. `MAX_CONVERT_BYTES` is the largest non-PDF file converted in memory.
    `MAX_OCR_RESPONSE_BYTES` caps one answer read back from the OCR service. `MAX_REQUEST_BODY_BYTES` is the
    ceiling on any request body read for the Idempotency-Key fingerprint: the largest body a route accepts (the trial
    PDF, 1 GiB; an upload part is 8 MiB)."""

    max_document_pages: int = 4000
    max_bundle_pages: int = 4000
    max_image_pixels: int = 100_000_000
    max_image_frames: int = 2000
    max_convert_bytes: int = 100 * 1024 * 1024
    max_ocr_response_bytes: int = 64 * 1024 * 1024
    max_request_body_bytes: int = 1024 * 1024 * 1024
    max_fingerprint_body_bytes: int = 16 * 1024 * 1024


def document_limits(env: Mapping[str, str] | None = None) -> DocumentLimits:
    source = os.environ if env is None else env
    defaults = DocumentLimits()
    bundle = env_int(source, "MAX_BUNDLE_PAGES", defaults.max_bundle_pages, minimum=1)
    document = env_int(source, "MAX_DOCUMENT_PAGES", min(defaults.max_document_pages, bundle), minimum=1)
    if document > bundle:
        raise ConfigError("MAX_DOCUMENT_PAGES must not be more than MAX_BUNDLE_PAGES")
    return DocumentLimits(
        max_document_pages=document,
        max_bundle_pages=bundle,
        max_image_pixels=env_int(source, "MAX_IMAGE_PIXELS", defaults.max_image_pixels, minimum=1),
        max_image_frames=env_int(source, "MAX_IMAGE_FRAMES", defaults.max_image_frames, minimum=1),
        max_convert_bytes=env_int(source, "MAX_CONVERT_BYTES", defaults.max_convert_bytes, minimum=1),
        max_ocr_response_bytes=env_int(source, "MAX_OCR_RESPONSE_BYTES", defaults.max_ocr_response_bytes, minimum=1),
        max_request_body_bytes=env_int(source, "MAX_REQUEST_BODY_BYTES", defaults.max_request_body_bytes, minimum=1),
        max_fingerprint_body_bytes=env_int(
            source, "MAX_FINGERPRINT_BODY_BYTES", defaults.max_fingerprint_body_bytes, minimum=1
        ),
    )


@dataclass(frozen=True)
class SandboxSettings:
    """The parser sandbox (`app.core.sandbox`): child processes that run PDF and image parsing. `pool_size` children,
    each recycled after `recycle_after` tasks (its replacement is started in the background first); a call is killed
    after `timeout_seconds`; a child may not grow past
    `memory_bytes` of address space."""

    pool_size: int = field(default_factory=lambda: min(4, max(2, os.cpu_count() or 2)))
    timeout_seconds: float = 120.0
    memory_bytes: int = 3 * 1024 * 1024 * 1024
    recycle_after: int = 500


def sandbox_settings(env: Mapping[str, str] | None = None) -> SandboxSettings:
    """`SANDBOX_POOL_SIZE` (the CPU count, at least 2, at most 4), `SANDBOX_TIMEOUT_SECONDS` (120),
    `SANDBOX_MEMORY_MB` (3072), `SANDBOX_RECYCLE_AFTER` (500)."""
    source = os.environ if env is None else env
    d = SandboxSettings()
    return SandboxSettings(
        pool_size=env_int(source, "SANDBOX_POOL_SIZE", d.pool_size, minimum=1),
        timeout_seconds=env_float(source, "SANDBOX_TIMEOUT_SECONDS", d.timeout_seconds, minimum=0.1),
        memory_bytes=env_int(source, "SANDBOX_MEMORY_MB", d.memory_bytes // (1024 * 1024), minimum=64) * 1024 * 1024,
        recycle_after=env_int(source, "SANDBOX_RECYCLE_AFTER", d.recycle_after, minimum=1),
    )


DEFAULT_SSE_SAFETY_POLL_SECONDS = 30.0


@dataclass(frozen=True)
class SseSettings:
    """Live-update streams: with a listener running, a stream looks again when told, and at least this often."""

    safety_poll_seconds: float = DEFAULT_SSE_SAFETY_POLL_SECONDS


def sse_settings(env: Mapping[str, str] | None = None) -> SseSettings:
    source = os.environ if env is None else env
    return SseSettings(
        safety_poll_seconds=env_float(source, "SSE_SAFETY_POLL_SECONDS", DEFAULT_SSE_SAFETY_POLL_SECONDS, minimum=0.1)
    )


DEFAULT_API_RATE_PER_MINUTE = 600
DEFAULT_API_MAX_STREAMS_PER_PERSON = 3


@dataclass(frozen=True)
class RateSettings:
    """Per-person limits (scalability PR 12); 0 turns a limit off.

    `per_minute` is the requests one person may make to one endpoint in a minute (Postgres counts, so every
    process and replica shares it). `streams_per_person` is the live event streams one person may hold open
    at once; it is counted in each process, which is enough at two processes.
    """

    per_minute: int = DEFAULT_API_RATE_PER_MINUTE
    streams_per_person: int = DEFAULT_API_MAX_STREAMS_PER_PERSON


def rate_settings(env: Mapping[str, str] | None = None) -> RateSettings:
    source = os.environ if env is None else env
    return RateSettings(
        per_minute=env_int(source, "API_RATE_PER_MINUTE", DEFAULT_API_RATE_PER_MINUTE, minimum=0),
        streams_per_person=env_int(source, "API_MAX_STREAMS_PER_PERSON", DEFAULT_API_MAX_STREAMS_PER_PERSON, minimum=0),
    )


@dataclass(frozen=True)
class ReaderSearchCacheSettings:
    """Bundle search (reader): decrypted words kept per case. At most `cases` cases (least recently used out),
    for `ttl_seconds`, and `max_words` words in all (the memory bound: about 100 MB per million words)."""

    cases: int = 8
    ttl_seconds: float = 600.0
    max_words: int = 3_000_000


def reader_search_cache_settings(env: Mapping[str, str] | None = None) -> ReaderSearchCacheSettings:
    source = os.environ if env is None else env
    return ReaderSearchCacheSettings(
        cases=env_int(source, "READER_SEARCH_CACHE_CASES", 8, minimum=1),
        ttl_seconds=env_float(source, "READER_SEARCH_CACHE_TTL_SECONDS", 600.0, minimum=1.0),
        max_words=env_int(source, "READER_SEARCH_CACHE_MAX_WORDS", 3_000_000, minimum=1),
    )


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
_IN_REGION_MODEL = re.compile(r"anthropic\.[a-z0-9][a-z0-9.\-]*(:\d+)?")


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
    window_seconds: int = 60  # the window `rpm` is counted over (Azure OCR counts submits per second: 1)


# temporary, dummy data only (ADR 0036): many small calls in parallel, not a few big ones
# A dense page's answer was cut off at 8000 tokens (the JSON ends mid-way and fails its schema). The direct
# client always streams (`anthropic_live`), so the SDK's large-`max_tokens` refusal does not apply: the cap is
# 32000, and a page cut off even on its own is asked once more with 64000 (the model's own ceiling).
# The API reserves each call's max_tokens against the per-minute output allowance when it admits the call, so
# concurrency x cap (50 x 32000 = 1.6M) is kept at the old 200 x 8000.
ANTHROPIC_LIMITS = ProviderLimits(
    rpm=1000, pages_per_call=3, max_output_tokens=32000, concurrency=50, widest_output_tokens=64000
)
BEDROCK_MAX_OUTPUT_TOKENS = 16000
# Default for `BEDROCK_CONCURRENCY`: the account-wide cap on Bedrock calls in flight at once (migration 0056).
BEDROCK_CONCURRENCY = 3
# Defaults for `AZURE_OCR_TPS` (submits a second, under the S0 tier's 15) and `AZURE_OCR_CONCURRENCY` (submits in
# flight at once): both counted across the whole account, like Bedrock's (scalability plan PR 4).
AZURE_OCR_TPS = 12
AZURE_OCR_CONCURRENCY = 30


@dataclass(frozen=True)
class CostGuard:
    """The cost guard's allowance (F11, K3): a case may use `multiple` x `expected_tokens_per_page` x the larger of
    its pages and `min_expected_pages` tokens. The one place the numbers, their defaults and the sum live: the AI
    kit (worker) trips the guard from `AiSettings.cost_guard`, built from `cost_guard()`, and the API reads
    `cost_guard()` for "used up its allowance", so `AI_COST_GUARD_MULTIPLE`, `AI_EXPECTED_TOKENS_PER_PAGE` and
    `AI_MIN_EXPECTED_PAGES` must be set identically on the API and the worker. Readable without the cell's AI
    endpoints (e.g. by a route that only needs to know whether a case is over its allowance)."""

    multiple: int = 3
    expected_tokens_per_page: int = 4000
    min_expected_pages: int = 100

    def allowance(self, pages: int) -> int:
        return self.multiple * self.expected_tokens_per_page * max(pages, self.min_expected_pages)


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
    expected_tokens_per_page: int = CostGuard.expected_tokens_per_page
    min_expected_pages: int = CostGuard.min_expected_pages
    cost_guard_multiple: int = CostGuard.multiple
    bedrock_rpm_limit: int = 5
    bedrock_concurrency: int = BEDROCK_CONCURRENCY
    azure_ocr_tps: int = AZURE_OCR_TPS
    azure_ocr_concurrency: int = AZURE_OCR_CONCURRENCY
    extraction_batch_size: int = 10
    # The share of a page's words the entries read from it must cover (env AI_ENTRY_COVERAGE_FLOOR, 0 to 1).
    entry_coverage_floor: float = 0.8
    # Temporary, dummy data only (ADR 0036). The defaults are today's behaviour: Bedrock only.
    primary_provider: str = "bedrock"
    direct_models: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    anthropic_endpoint: str = ""

    @property
    def cost_guard(self) -> CostGuard:
        """The three guard numbers as the one `CostGuard`, the only place the allowance is worked out."""
        return CostGuard(
            multiple=self.cost_guard_multiple,
            expected_tokens_per_page=self.expected_tokens_per_page,
            min_expected_pages=self.min_expected_pages,
        )

    @property
    def limits(self) -> Mapping[str, ProviderLimits]:
        """Each provider's own limits. `anthropic` is present only while it is the primary (ADR 0036)."""
        found = {
            "bedrock": ProviderLimits(
                rpm=self.bedrock_rpm_limit,
                pages_per_call=self.extraction_batch_size,
                max_output_tokens=BEDROCK_MAX_OUTPUT_TOKENS,
                concurrency=self.bedrock_concurrency,
            ),
            # The OCR limiter reuses the slot scheduler; pages and tokens do not apply to it.
            "azure_ocr": ProviderLimits(
                rpm=self.azure_ocr_tps,
                pages_per_call=1,
                max_output_tokens=0,
                concurrency=self.azure_ocr_concurrency,
                window_seconds=1,
            ),
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
    return _IN_REGION_MODEL.fullmatch(model_id) is not None


def endpoint_allowed(url: str, allowed_hosts: frozenset[str]) -> bool:
    """An address the cell may talk to: `https` or `wss`, a host on the allow-list, the default port, no credentials.

    Fails closed on anything two parsers could read differently: padding, control characters and backslashes are
    refused, and the host the HTTP client's own parser (urllib3, which the AWS SDK uses) finds must be the host
    checked here (AUDIT-14 M3 to M6)."""
    if url != url.strip() or "\\" in url or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
        client_host = parse_url(url).host
    except (ValueError, LocationParseError):
        return False
    if parts.scheme not in ("https", "wss") or parts.hostname is None or parts.hostname not in allowed_hosts:
        return False
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return False
    if port not in (None, 443):
        return False
    return client_host is not None and client_host.lower() == parts.hostname


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
    # The model service's host names the region it serves; it must be the one the cell signs for (AUDIT-14 M2).
    if values["AWS_REGION"].lower() not in (urlsplit(values["BEDROCK_ENDPOINT"]).hostname or "").split("."):
        raise ConfigError("BEDROCK_ENDPOINT is not in the region named by AWS_REGION")

    def number(name: str, default: int) -> int:
        raw = source.get(name, "").strip()
        value = _whole(raw) if raw else default
        if value is None:
            raise ConfigError(f"{name} must be a whole number")
        if value < 1:
            raise ConfigError(f"{name} must be at least 1")
        return value

    def share(name: str, default: float) -> float:
        raw = source.get(name, "").strip()
        value = _decimal(raw) if raw else default
        if value is None:
            raise ConfigError(f"{name} must be a number")
        if not 0 < value <= 1:  # also false for nan
            raise ConfigError(f"{name} must be more than 0 and at most 1")
        return value

    guard = cost_guard(source)  # the same numbers the API reads: one set of defaults and one set of limits
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
        expected_tokens_per_page=guard.expected_tokens_per_page,
        min_expected_pages=guard.min_expected_pages,
        cost_guard_multiple=guard.multiple,
        bedrock_rpm_limit=number("BEDROCK_RPM_LIMIT", 5),
        bedrock_concurrency=number("BEDROCK_CONCURRENCY", BEDROCK_CONCURRENCY),
        azure_ocr_tps=number("AZURE_OCR_TPS", AZURE_OCR_TPS),
        azure_ocr_concurrency=number("AZURE_OCR_CONCURRENCY", AZURE_OCR_CONCURRENCY),
        extraction_batch_size=number("AI_EXTRACTION_BATCH_SIZE", 10),
        entry_coverage_floor=share("AI_ENTRY_COVERAGE_FLOOR", AiSettings.entry_coverage_floor),
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
    wake_s: float = 120.0  # how long a connect keeps trying while the service sleeps (ADR 0027)


def clamd_settings(env: Mapping[str, str] | None = None) -> ClamdSettings:
    source = os.environ if env is None else env
    host = source.get("CLAMD_HOST", "").strip()
    if not host:
        raise ConfigError("CLAMD_HOST is not set")
    try:
        port = env_int(source, "CLAMD_PORT", 3310, minimum=1)
        timeout_s = float(source.get("CLAMD_TIMEOUT_S", "").strip() or 60)
        wake_s = float(source.get("CLAMD_WAKE_S", "").strip() or 120)
    except ValueError as exc:
        raise ConfigError("CLAMD_PORT, CLAMD_TIMEOUT_S and CLAMD_WAKE_S must be numbers") from exc
    if not 0 <= wake_s < float("inf"):
        raise ConfigError("CLAMD_WAKE_S must be zero or more seconds")
    return ClamdSettings(host=host, port=port, timeout_s=timeout_s, wake_s=wake_s)
