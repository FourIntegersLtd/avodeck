"""The server's own access log is off: requests are logged by `RequestLog` by route template (rule 6).

Uvicorn's access lines carry the raw path and query, and its formatter needs the record's `args`,
which the scrubbing record factory removes, so each line used to fail with a logging error.
"""

from __future__ import annotations

import logging

from app.core import logs


def test_configure_switches_off_the_servers_access_log() -> None:
    logs.configure()
    assert logging.getLogger("uvicorn.access").disabled
