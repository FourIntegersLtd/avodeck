"""App factory: error handlers, the foundation routers and, later, each slice's router."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.core.auth.identity import IdentityProvider
from app.core.config import cell_settings, database_settings
from app.core.db import Database
from app.core.errors import install_error_handlers
from app.core.routes.cases import cases_router
from app.core.routes.me import me_router


def create_app(
    *,
    database: Database | None = None,
    identity_provider: IdentityProvider | None = None,
    region: str | None = None,
) -> FastAPI:
    """Without a database or region they are read from the environment at start-up.

    Without an identity provider every request is signed out (the auth step adds the cookie session).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned: Database | None = None
        if app.state.region is None:
            app.state.region = cell_settings().region
        if app.state.database is None:
            owned = Database(database_settings().url)
            await owned.open()
            app.state.database = owned
        try:
            yield
        finally:
            if owned is not None:
                await owned.close()

    app = FastAPI(title="Avodeck API", lifespan=lifespan)
    app.state.database = database
    app.state.identity_provider = identity_provider
    app.state.region = region
    install_error_handlers(app)
    for r in (me_router, cases_router):
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
