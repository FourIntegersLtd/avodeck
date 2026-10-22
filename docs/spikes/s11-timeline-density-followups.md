# S11 timeline: deferred density interactions

**Status:** spike (partially shipped). Activity tab delivers LabVis-style density; items below remain deferred.

## Context

After S11 shipped **time window first** and **chronology as the default reading surface**, the swim-lane map remains useful for pattern spotting (lane mix, gaps, clusters) within the selected window—not for reading every consultation line.

## Shipped: Activity tab

The **Activity** view (`TimelineActivity.tsx`, `ActivityDensityStrip.tsx`, `activityBuckets.ts`) pairs a **single proportional density strip** (stacked by pack lane colour) with a **chronology list** for the current time window—aligned with LabVis’s time-slider density graph plus document list (BMC 2025). A plain-language summary highlights total dated entries and the busiest period. Optional **Show breakdown by type** (`StackedBar`) replaces the earlier multi-row histogram. Tapping a strip segment narrows **From/To** and filters the list on the same tab.

## Candidate follow-ups

### Same-day clusters on the map

**Reference:** HumanGraph grouped same-date markers; NHS Lifelines icon stacks.

**Idea:** When more than one verified/suggested event shares the same calendar day on a lane, render one marker (“3 on 28 Jun 1994”) that expands in the chronology list or opens a small disambiguation list.

**Risks:** Pack lanes differ; cluster copy must use i18n keys only. Accessibility: cluster control must expose count and keyboard path to each entry.

**Eval:** Synthetic bundle with 5 GP visits same week; task “how many visits in June 1994?” without horizontal scroll.

### Axis brush to set `from` / `to`

**Reference:** Health Timeline adjustable start/end; Apple Charts time scope.

**Idea:** Drag on the month axis (overview map tab) to set API `from`/`to`, mirroring the time window bar.

**Risks:** Touch targets for GPs (≥52px); must not fight horizontal scroll on the board. Prefer pointer-up to commit range, with visible preview band.

**Dependency:** Map tab only; chronology list already respects server filters.

## Out of scope here

- Replacing pack lanes with a single generic timeline (breaks packs rule).
- Custom D3 chart in v1 (use list + existing board).

## Trigger to implement

- User testing: experts still struggle to narrow a window without opening date fields, **or**
- Support tickets about “too many dots” after chronology default, **and** window is already &lt;90 days on the map.
