"""When a page counts as set aside as a copy (PV-09).

A page is set aside as a copy only once the expert has confirmed it (`page_analysis.duplicate_status = 'confirmed'`).
A copy the pipeline merely suggested, or one the expert chose to keep, is still read and still counted. The fragment
needs only the pages table aliased `p`, so every reader query can use it as it is.

Pipeline keeps the same rule as `NOT_SET_ASIDE` (a join on `a`); when foundation's shared set-aside helper lands in
`app/core/contracts/`, this fragment is the one place reader switches over.
"""

from __future__ import annotations

CONFIRMED_COPY = (
    "(p.duplicate_of_page_id is not null and exists (select 1 from app.page_analysis dup"
    " where dup.page_id = p.id and dup.duplicate_status = 'confirmed'))"
)
