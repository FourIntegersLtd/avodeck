"""The bundle pipeline's entry points (slice A implements them; B and G queue them).

Queue with `registry.enqueue(conn, case_id, PROCESS_BUNDLE)` (whole bundle) or `PROCESS_PAGES`
(appended pages only). Pages keep their numbers forever; appended pages continue from the last.
"""

from __future__ import annotations

PROCESS_BUNDLE = "process_bundle"
PROCESS_PAGES = "process_pages"
