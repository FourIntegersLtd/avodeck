"""App factory: error handlers, the foundation routers and each slice's router (`app.features`)."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.core import logs
from app.core.auth.supabase import SupabaseAuth
from app.core.config import ConfigError, auth_settings, cell_settings, database_settings
from app.core.contracts.reading import PageReadLog
from app.core.contracts.today import NeedsYouRegistry
from app.core.contracts.today import needs_you as default_needs_you
from app.core.crypto import CaseCrypto, key_provider_from_env
from app.core.db import Database
from app.core.email import Mailer, OutboxMailer
from app.core.errors import install_error_handlers
from app.core.features import DEFAULT_PACKAGE as DEFAULT_FEATURES
from app.core.features import feature_routers, start_features
from app.core.packs import PackLibrary, default_library
from app.core.routes.account import account_public, account_router
from app.core.routes.auth import auth_pending, auth_public, auth_signed_in, auth_status
from app.core.routes.cases import cases_router
from app.core.routes.cell import cell_public_router
from app.core.routes.dictation import dictation_socket, transcripts_router
from app.core.routes.jobs import jobs_router
from app.core.routes.me import me_router
from app.core.routes.notifications import notifications_router
from app.core.routes.packs import case_pack_router, packs_router
from app.core.routes.today import today_router
from app.core.routes.uploads import uploads_router
from app.core.speech import SpeechClient
from app.core.storage import SupabaseStorage, storage_settings
from app.features.pipeline import pipeline_router
from app.features.reader.log import ReaderPageReadLog
from app.features.report.routes_colleague import colleague_router
from app.features.trial.routes_webhook import webhook_router as esign_webhook_router

# Slice B's read log is the app's own; a test passing `page_reads=None` gets a gate that stays shut.
READER_LOG = ReaderPageReadLog()


def create_app(
    *,
    database: Database | None = None,
    region: str | None = None,
    auth: SupabaseAuth | None = None,
    mailer: Mailer | None = None,
    packs: PackLibrary | None = None,
    storage: SupabaseStorage | None = None,
    crypto: CaseCrypto | None = None,
    speech: SpeechClient | None = None,
    needs_you: NeedsYouRegistry | None = None,
    page_reads: PageReadLog | None = READER_LOG,
    features: str = DEFAULT_FEATURES,
) -> FastAPI:
    """Without a database, region or Supabase Auth they are read from the environment at start-up.

    Storage and case encryption are also read from the environment when not passed; if the cell is
    missing them, file routes answer 503 until it has them. Speech has no live client yet (no cloud
    accounts): without one, dictation answers `speech_unavailable` and the expert types.

    Without a mailer, mail goes to the in-memory stand-in (`OutboxMailer`) until the cell has Resend.
    Today lists the "needs you" items registered on `needs_you` (the app's own registry unless a test
    passes one). `page_reads` is slice B's read log unless a test passes another; with `None` the
    blind-read gate stays shut (rule 3).
    Packs load here, not in the lifespan: an invalid pack stops the app being built at all.
    Each slice's router under `features` (`app.features.<name>.router`) is mounted with `mount()`.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned: Database | None = None
        if app.state.region is None:
            app.state.region = cell_settings().region
        dsn = os.environ.get("SENTRY_DSN", "").strip()
        if dsn:
            logs.init_sentry(dsn, environment=os.environ.get("SENTRY_ENVIRONMENT", app.state.region))
        if app.state.auth is None:
            app.state.auth = SupabaseAuth(auth_settings())
            app.state.app_origin = app.state.auth.settings.app_origin
        if app.state.database is None:
            owned = Database(database_settings().url)
            await owned.open()
            app.state.database = owned
        owned_storage: SupabaseStorage | None = None
        if app.state.storage is None:
            try:
                owned_storage = SupabaseStorage(storage_settings())
            except ConfigError:
                owned_storage = None
            app.state.storage = owned_storage
        if app.state.crypto is None:
            try:
                app.state.crypto = CaseCrypto(key_provider_from_env())
            except ConfigError:
                app.state.crypto = None
        if app.state.storage is not None:
            try:
                await app.state.storage.ensure_bucket()
            except Exception as exc:  # noqa: BLE001 - start-up goes on; file routes answer for themselves
                logging.getLogger(__name__).warning("storage bucket check failed: %s", type(exc).__name__)
        try:
            await start_features(features, app)  # each slice's `on_startup(app)`, database ready
            yield
        finally:
            if owned_storage is not None:
                await owned_storage.aclose()
            if owned is not None:
                await owned.close()

    app = FastAPI(title="Avodeck API", lifespan=lifespan)
    app.state.database = database
    app.state.region = region
    app.state.auth = auth
    app.state.app_origin = auth.settings.app_origin if auth is not None else None
    app.state.mailer = mailer if mailer is not None else OutboxMailer()
    app.state.packs = packs if packs is not None else default_library()
    app.state.storage = storage
    app.state.crypto = crypto
    app.state.speech = speech
    app.state.needs_you = needs_you if needs_you is not None else default_needs_you
    app.state.page_reads = page_reads
    app.state.job_poll_seconds = 1.0  # how often a progress stream looks at its job
    app.state.job_stream_seconds = 300.0  # then the stream ends and the browser reconnects
    logs.configure(app.state.packs.identifier_finders())
    app.add_middleware(logs.RequestLog)
    install_error_handlers(app)
    routers = (
        cell_public_router,
        auth_public,
        auth_pending,
        auth_status,
        auth_signed_in,
        account_public,
        account_router,
    )
    files = (uploads_router, jobs_router, transcripts_router, dictation_socket)
    people = (me_router, today_router, notifications_router)
    slices = (pipeline_router, colleague_router, esign_webhook_router)  # public: token- or signature-gated
    for r in (*routers, *people, cases_router, packs_router, case_pack_router, *files, *slices):
        mount(app, r)
    for r in feature_routers(features):
        mount(app, r)
    return app


def mount(app: FastAPI, router: APIRouter) -> None:
    """Add a router's routes to the app itself, not as a lazily included router.

    FastAPI includes routers lazily, so `app.routes` would not list their routes and the rule 9
    guard (tests/guards/test_foundation_case_routes.py) could not see them. Every slice's router
    is mounted with this.
    """
    app.router.routes.extend(router.routes)
    mark_changed = getattr(app.router, "_mark_routes_changed", None)
    if mark_changed is not None:
        mark_changed()


app = create_app()
