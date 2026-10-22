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

## Building it in parallel

Five streams can run at once, one Claude session (or developer) each, in its own git worktree. Today they
would all edit the same few files: `Timeline.tsx`, `TimelineBoard.tsx`, `storyLayout.ts`, `words.ts`,
`understand.css`, `routes_timeline.py` and the generated client (`openapi.json`, `schema.ts`). A short
**seam** pull request goes first and splits those shared files, so each stream then owns its own files only.

### Step 1 · Seam (one session, about 1 day, merged before the streams start)

Owner: understand-owner, with foundation-owner reviewing the two foundation-owned files. It changes no
behaviour: every new part returns "nothing" until its stream fills it in. All existing tests stay green.

1. **Every API shape at once**, so the generated client is regenerated once, here, and never in a stream:
   - `Timeline.gaps: list[Gap] = []`, `Event.series_key: str | None = None`, `Lane.series: list[Series] = []`.
   - `GET /cases/{case_id}/timeline/overview` returning the `Overview` model, built from a stub in the new `overview.py` that returns empty lanes.
   - `routes_timeline.py` calls `gaps.build(...)` (new `gaps.py`, returns `[]`) and `series.build(...)` (new `series.py`, returns `[]`). Neither stream touches the route again.
   - `make api-client`; architecture 5.2 row and data dictionary entries; `make check-spec`.
2. **Foundation pieces, as interfaces with harmless defaults:**
   - `api/app/core/contracts/record_gaps.py`: `gaps_overlapping(conn, case_id, start, end) -> list[RecordGap]`, an entry point that returns `[]` until slice A registers its reader.
   - `packs/schema/lanes.schema.json`: optional `series` on a lane (no pack uses it yet); `pack.lane_series(key)` in `app/core/packs.py`.
3. **Web parts split into their own files**, each a stub the board already calls:
   - `GapConnectors.tsx`: today's gap drawing moves here unchanged. The board renders `<GapConnectors lane=… cards=… gaps=… />`.
   - `SeriesBars.tsx`: renders nothing yet. `layoutLane` gains a `reservedRows` option (default 0).
   - `QuestionChip.tsx`: wraps `QChip`. Every chip on the board, ribbon and panel uses it, so the highlight lives in one file. `highlight.tsx` provides an empty context.
   - `useCardMotion.ts`: a no-op hook the board calls with its ref and the window.
   - The ribbon fetches through `useRibbonData.ts`, which for now still calls `/timeline` with no window.
4. **Words and styles per stream:** each stream registers its words in its own `*.words.ts` (with `registerMessages`) and its styles in its own `*.css`, imported from its component. So no stream edits `words.ts` or `understand.css`.
5. **Tests per stream:** each stream writes new test files. None edits `Timeline.test.tsx` or `storyLayout.test.ts`.

### Step 2 · Five streams, in parallel

| Stream | Builds | Owns these files only | Owner agent | Waits on |
|---|---|---|---|---|
| **A · Ribbon data** | Item 1 | `api/.../understand/overview.py`, `tests/http/test_understand_routes_overview.py`; `web/.../useRibbonData.ts` (+ test) | understand-owner | Seam |
| **B · Highlight** | Item 2 | `QuestionChip.tsx`, `highlight.tsx`, `highlight.css`, `highlight.words.ts` (+ tests) | understand-owner | Seam |
| **C · Motion** | Item 3 | `useCardMotion.ts`, `motion.css`, the `moves()` helper in new `motion.ts` (+ tests) | understand-owner | Seam |
| **D · Gaps** | Item 4 | `api/.../understand/gaps.py`, `tests/unit/test_understand_gaps_service.py`, `tests/http/test_understand_routes_gaps.py`; `GapConnectors.tsx`, `gaps.words.ts`, `gaps.css` (+ tests) | understand-owner; pipeline-owner adds the `record_gaps` reader in slice A's own folder | Seam; wording approval before merge |
| **E · Series** | Item 5 | `api/.../understand/series.py` (+ tests); `packs/specialty/general-practice/lanes.yaml`, a fixture pack's `lanes.yaml`; `SeriesBars.tsx`, `series.words.ts`, `series.css` (+ tests) | understand-owner; a pack reviewer signs off the launch pack | Seam; repeat-rule decision before merge |

Why they don't collide:
- No two streams edit the same file. Only the seam touches the shared ones.
- No stream regenerates the API client. If a stream finds it needs a new field, it stops and asks, and the seam owner adds it in a small follow-up that every stream rebases on.
- Each stream's tests sit in its own files, so the red-then-green check pairs each `test` commit with its own `feat` commit.
- Stream D's server code reads record gaps only through the contract. Until slice A registers the real reader it gets `[]`, so D's tests use the entry point's `override()` and don't wait on A.

Branches and worktrees:
```
git worktree add ../avodeck-seam   -b slice/d-timeline-seam
# after the seam merges:
git worktree add ../avodeck-ribbon -b slice/d-timeline-ribbon
git worktree add ../avodeck-hl     -b slice/d-timeline-highlight
git worktree add ../avodeck-motion -b slice/d-timeline-motion
git worktree add ../avodeck-gaps   -b slice/d-timeline-gaps
git worktree add ../avodeck-series -b slice/d-timeline-series
```

### Step 3 · Join up (about 1 day)

- Merge the streams in any order; each rebases cleanly because none share files.
- Run the full Playwright suite for S11 with all five on: axe, 52 px buttons, 17 px text, 1,500 and 3,691 entries.
- Redraw the S11 canvas board once, and update the slice D brief.

### Time

| Step | One developer | Run in parallel |
|---|---|---|
| Seam | 1 day | 1 day |
| Streams | 12–15 days back to back | 4–5 days (the longest is E) |
| Join up | 1 day | 1 day |
| **Total** | **about 3 weeks** | **about 1½ weeks** |

The two decisions (gap wording; what counts as the same repeat and how long a pause ends a bar) block
only the merge of D and E, not their start.
