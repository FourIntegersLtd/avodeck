"""A made-up features package on disk, for the feature-mounting tests.

`dummy_features(root)` writes `<name>/dummy/{__init__,router,jobs}.py` (a case route and a module the
worker must import) plus `<name>/quiet/` (a feature with no router), puts `root` on `sys.path` and
returns the package name. Each call uses a fresh name so imports never leak between tests.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROUTER = """
from typing import Annotated

from fastapi import Depends

from app.core.auth.access import CaseAccess, require_case_access
from app.core.http import router as make_router

router = make_router()


@router.get("/v1/cases/{case_id}/zz-dummy")
async def zz_dummy(access: Annotated[CaseAccess, Depends(require_case_access("case.view_content"))]) -> dict[str, str]:
    return {"zz": "ok"}
"""

JOBS = "LOADED = True\n"


def dummy_features(root: Path) -> str:
    name = f"zz_features_{uuid.uuid4().hex[:10]}"
    package = root / name
    (package / "dummy").mkdir(parents=True)
    (package / "quiet").mkdir()
    (package / "__init__.py").write_text("")
    (package / "dummy" / "__init__.py").write_text("")
    (package / "dummy" / "router.py").write_text(ROUTER)
    (package / "dummy" / "jobs.py").write_text(JOBS)
    (package / "quiet" / "__init__.py").write_text("")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return name
