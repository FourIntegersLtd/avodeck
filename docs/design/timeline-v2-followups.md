# Timeline v2: the five follow-ups

Branch `slice/d-timeline-v2` shipped the ribbon, day cards, Board/List and the side panel (screen only).
This plan builds the five pieces left over. Each is one small pull request, test-first (`tdd-feature`),
with `verify-slice` and the reviewer checklist before merging.

| # | Piece | Where the work is | Needs another owner | Size |
|---|---|---|---|---|
| 0 | Open the v2 pull request and get CI green | CI | – | ½ day |
| 1 | Lighter ribbon: `/timeline/overview` | API + web (D) | – | 2 days |
| 2 | Question highlight on hover | Web (D) | – | 1–1½ days |
| 3 | Smooth zoom | Web (D) | – | 2 days |
| 4 | Gap wording from the server | API + web (D), a read contract | foundation-owner (contract), pipeline-owner (A) reviews | 3–4 days |
| 5 | Repeat-prescription bars | Pack schema, packs, API + web (D) | foundation-owner (schema, loader), pack reviewer | 4–5 days |

About 2½–3 weeks for one developer, or 1½–2 weeks with two working in parallel (1–3 alongside 4–5).

## 0. Land v2 first

- Open the pull request for `slice/d-timeline-v2` with the ticked `docs/spec/reviewer-checklist.md`.
- Watch the server tests, the red-then-green check and Playwright.
- The 1,500-entry test now has a flat 1 s budget (the old test allowed 1.5 s on CI). If a GitHub runner misses it, restore the CI allowance in a `fix(ci)` commit rather than slowing the screen down.

## 1. Lighter ribbon: `GET /v1/cases/{case_id}/timeline/overview`

**Why:** the ribbon loads every entry a second time just to draw counts.

**Server** (`api/app/features/understand/routes_timeline.py`):
- Same access check as `/timeline` (`require_case_access("case.view_content")`) and the same `repo.visible_facts`, so unread pages' suggestions stay hidden (rule 3).
- Optional `as_seen_on`; no window or status filters (the ribbon always shows the whole record).
- Reply:
  ```
  { range: {first, last} | null,
    months: ["2004-01", …],
    lanes: [{ key, label, colour, counts: [int per month] }],
    pins: [{ fact_id, date, lane_key, has_finding, question_ids }] }   // things to look at and question links only
  ```
- Lanes, order and labels from `pack.lanes()` through `arrange()`, exactly as `/timeline` does.

**Tests first:**
- `api/tests/http/test_understand_routes_timeline.py`: counts per lane per month; unread pages' suggestions not counted; `as_seen_on` drops later arrivals; 404 for someone without access; fixture packs give the fixture lanes.
- Web: the ribbon draws from `/overview`, and no `/timeline` request goes out without a window.

**Also:** architecture 5.2 endpoint row, `make api-client`, `make check-spec`. Schemathesis picks the route up on its own.

## 2. Question highlight on hover

**Why:** "show me everything for Q2" without hiding the rest.

- Pointing at or tabbing to a question chip (on a pill, a card or the side panel) lights every entry linked to that question across the lanes and fades the others to 35%. Ribbon pins for that question get a ring.
- It isn't shown by colour alone: matching cards get an outline, and the board's count line reads "Q2: 7 entries in this period".
- On touch screens hover doesn't exist, so the question pills (which filter) stay the way in.
- Screen only: `TimelineBoard`, `TimelineRibbon` and a small `highlight` state in `Timeline.tsx`.

**Tests first:** focusing the Q2 chip marks Q2's cards `data-highlight` and the rest `data-faded`; Escape or moving away clears it; axe passes.

## 3. Smooth zoom

**Why:** when the window changes, the board jumps and the eye loses its place.

- When new data arrives for a new window, each card slides from its old place to its new one (the FLIP technique, keyed by fact id). New cards fade in, and cards that leave fade out. The axis labels slide the same way. 250 ms in total.
- While the window is being dragged, a light band on the board shows the stretch about to be shown.
- Off under `prefers-reduced-motion`.
- A pure helper `moves(before, after)` in `storyLayout.ts` works out who moves, enters and leaves.

**Tests first:** the helper's three lists; no transition style with reduced motion. In Playwright, a card keeps its identity (same `data-facts`) across a window change.

## 4. Gap wording from the server

**Why:** "86 days to the next entry" is a count. The expert needs to know whether nothing was done, or whether the records for that stretch may be missing. Only the server can tell.

**Server:**
- Add `gaps` to `Timeline` (contracts only grow):
  ```
  gaps: [{ finding_id, from_fact_id, to_fact_id | null, days, kind, label_key }]
  ```
- Built from open findings (`app.findings`):
  - `from`: the finding's first evidence fact.
  - `to`: the first later fact of one of the check's own `action_kinds` (from `checks.yaml`), else the next fact of any kind, else none (the gap runs to the end of the window).
  - `kind`: `records_quiet` when one of slice A's `record_gaps` for that source overlaps the stretch. Otherwise the finding's rule (`no_action_within`, `not_acknowledged_within`, …).
- Reading `record_gaps` needs a small read-only contract, `api/app/core/contracts/record_gaps.py` (`gaps_overlapping(conn, case_id, start, end)`). foundation-owner adds it, and pipeline-owner (slice A) registers the reader.

**Words** (i18n keys, not pack words, and no opinion; rule 1 untouched because no AI is involved):
- `timeline.gap.no_action_within`: "{{count}} days. Nothing in the record shows what followed."
- `timeline.gap.not_acknowledged_within`: "{{count}} days. Nothing shows this was acknowledged."
- `timeline.gap.records_quiet`: "{{count}} days. The records go quiet here, so pages may be missing."
- `timeline.gap.none_after`: "Nothing in the record after this."

**Web:** draw connectors from `gaps` only, and remove the screen's own next-entry guess and the 14-day cut-off.

**Tests first:**
- API: each rule gives its kind; an overlapping record gap turns it into `records_quiet`; a decided finding (not open) draws no gap; fixture packs work.
- Web: the label shown matches the kind.

**Decision needed:** the founder approves the four sentences against `docs/spec/glossary.md`.

## 5. Repeat-prescription bars

**Why:** 669 prescriptions in the test case are mostly the same medicine reissued. One bar with a tick per issue replaces dozens of cards.

**Pack** (foundation-owned schema and loader; the packs themselves need a reviewer):
- `packs/schema/lanes.schema.json`: an optional `series` on a lane object:
  ```yaml
  - key: prescriptions
    kinds: [prescription]
    series: {kinds: [prescription], same: [drug, dose], max_gap_days: 120, min_count: 2}
  ```
- The launch specialty pack gets the rule above. A fixture pack gets a series on a different kind and field, to prove nothing is hard-coded (rule 11).
- `app/core/packs.py` exposes it; `lanes.py` gains `series_of(facts, lane)`. It compares the copied words after normalising case and spacing, and starts a new series when the gap between issues is longer than `max_gap_days`.

**Server:**
- `Event.series_key: str | None`.
- `Lane.series: [{ key, label, first, last, count, fact_ids }]`, with `label` made from the page's own words (drug and dose), never prose.

**Web:**
- A series is drawn as a bar from first to last issue, with a tick per issue and its label on the bar.
- Issues in a series don't get cards, except ones that are a thing to look at or linked to a question, which still get a card above the bar.
- The bar is a 52 px button that opens all the issues in the side panel.
- `layoutLane` reserves the bar's row before packing cards.

**Tests first:**
- Pack: schema accepts and rejects; the fixture pack's series changes the bars with no code change.
- API: grouping by drug and dose; a long pause starts a new series; one issue alone is not a series.
- Web: the layout keeps cards off the bar row, and the bar opens the panel with every issue.

**Decisions needed:**
- What counts as the same repeat. Drug and dose is proposed. Should a dose change start a new bar?
- How long a pause ends a bar. 120 days is proposed.

## Order

1. Item 0, then items 1, 2 and 3 (slice D only, no waiting on anyone).
2. Ask foundation-owner for the `record_gaps` contract and the lanes schema change at the start, so items 4 and 5 aren't blocked later.
3. Item 4, then item 5 (it needs the pack review).
4. Redraw the S11 canvas board once, at the end, to match.
