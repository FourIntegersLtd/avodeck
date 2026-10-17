"""App factory: error handlers, the foundation routers and each slice's router (`app.features`)."""

from __future__ import annotations

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
from app.core.features import feature_routers
from app.core.packs import PackLibrary, default_library
from app.core.routes.account import account_public, account_router
from app.core.routes.auth import auth_pending, auth_public, auth_signed_in, auth_status
from app.core.routes.cases import cases_router
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
    page_reads: PageReadLog | None = None,
    features: str = DEFAULT_FEATURES,
) -> FastAPI:
    """Without a database, region or Supabase Auth they are read from the environment at start-up.

    Storage and case encryption are also read from the environment when not passed; if the cell is
    missing them, file routes answer 503 until it has them. Speech has no live client yet (no cloud
    accounts): without one, dictation answers `speech_unavailable` and the expert types.

    Without a mailer, mail goes to the in-memory stand-in (`OutboxMailer`) until the cell has Resend.
    Today lists the "needs you" items registered on `needs_you` (the app's own registry unless a test
    passes one). Without `page_reads` (slice B's read log) the blind-read gate stays shut (rule 3).
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
        try:
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
    routers = (auth_public, auth_pending, auth_status, auth_signed_in, account_public, account_router)
    files = (uploads_router, jobs_router, transcripts_router, dictation_socket)
    people = (me_router, today_router, notifications_router)
    slices = (pipeline_router,)
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
