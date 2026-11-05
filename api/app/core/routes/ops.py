"""GET /v1/internal/metrics: pool, event-loop lag, provider 429s, queue depth and job run times, for operators.

Not for the browser (rule 4): it needs `Authorization: Bearer <OPS_METRICS_TOKEN>`. Without the setting, or with
a missing or wrong token, the route answers 404 as if it did not exist. Numbers and short codes only (rule 6).
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core import metrics
from app.core.errors import ApiError
from app.core.http import public_router

ops_router = public_router(machine=True, prefix="/v1/internal", tags=["ops"])


def _allowed(request: Request) -> bool:
    token = os.environ.get("OPS_METRICS_TOKEN", "").strip()
    header = request.headers.get("authorization", "")
    scheme, _, given = header.partition(" ")
    if not token or scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(given.strip().encode(), token.encode())


@ops_router.get("/metrics")
async def get_metrics(request: Request) -> JSONResponse:
    if not _allowed(request):
        raise ApiError("not_found", "Not found.", 404)
    body: dict[str, Any] = await metrics.collect(request.app.state.database)
    return JSONResponse(body, headers={"Cache-Control": "no-store"})
