import { useMemo, useState } from "react";
import type { Schemas } from "../../foundation/platform-client";
import { StackedBar } from "../../foundation/ui/Progress";
import { ActivityDensityStrip } from "./ActivityDensityStrip";
import { chronologyEntries } from "./chronologyEntries";
import { longDate } from "./shared";
import { ChronologyList } from "./TimelineChronology";
import {
  activityBuckets,
  busiestBucket,
  entriesInBucket,
  isActivityEvenlySpread,
  totalDatedInBuckets,
  type ActivityBucket,
} from "./activityBuckets";
import { w } from "./words";

type TimelineData = Pick<Schemas["Timeline"], "lanes" | "unplaced">;

function laneTotalsInWindow(buckets: ActivityBucket[], laneKey: string): number {
  return buckets.reduce((n, b) => n + (b.byLane[laneKey] ?? 0), 0);
}

export function TimelineActivity({
  caseId,
  data,
  numbers,
  windowFrom,
  windowTo,
  onNarrowWindow,
  onResetWindow,
  onOpenChronology,
}: {
  caseId: string;
  data: TimelineData;
  numbers?: Record<string, number>;
  windowFrom: string;
  windowTo: string;
  onNarrowWindow: (from: string, to: string) => void;
  onResetWindow: () => void;
  onOpenChronology: () => void;
}) {
  const [selected, setSelected] = useState<{ from: string; to: string } | null>(null);
  const { buckets, undatedCount, windowFrom: wf, windowTo: wt } = useMemo(
    () => activityBuckets(data.lanes, data.unplaced, { from: windowFrom || undefined, to: windowTo || undefined }),
    [data.lanes, data.unplaced, windowFrom, windowTo],
  );

  const lanesWithEvents = useMemo(
    () => data.lanes.filter((l) => l.events.some((e) => (e.event_date ?? e.available_at) !== null)),
    [data.lanes],
  );

  const maxTotal = Math.max(1, ...buckets.map((b) => b.total));
  const allEntries = useMemo(() => chronologyEntries(data.lanes, data.unplaced), [data.lanes, data.unplaced]);
  const listEntries = useMemo(() => {
    if (!selected) return allEntries;
    return entriesInBucket(allEntries, selected);
  }, [allEntries, selected]);

  const totalDated = totalDatedInBuckets(buckets);
  const busiest = busiestBucket(buckets);
  const even = isActivityEvenlySpread(buckets);

  const pickBucket = (bucket: ActivityBucket) => {
    if (selected?.from === bucket.from && selected.to === bucket.to) {
      setSelected(null);
      onResetWindow();
      return;
    }
    setSelected({ from: bucket.from, to: bucket.to });
    onNarrowWindow(bucket.from, bucket.to);
  };

  const clearSelection = () => {
    setSelected(null);
    onResetWindow();
  };

  const breakdownBucket = selected
    ? buckets.find((b) => b.from === selected.from && b.to === selected.to)
    : null;

  const breakdownSegments = useMemo(() => {
    const source = breakdownBucket ?? null;
    if (!source) {
      return lanesWithEvents
        .map((lane) => ({ key: lane.key, value: laneTotalsInWindow(buckets, lane.key), color: lane.colour ?? "var(--ink-soft)", label: lane.label }))
        .filter((s) => s.value > 0);
    }
    return lanesWithEvents
      .map((lane) => ({
        key: lane.key,
        value: source.byLane[lane.key] ?? 0,
        color: lane.colour ?? "var(--ink-soft)",
        label: lane.label,
      }))
      .filter((s) => s.value > 0);
  }, [breakdownBucket, buckets, lanesWithEvents]);

  if (buckets.length === 0) {
    return (
      <section className="av-understand-activity" data-testid="timeline-activity">
        <p className="av-muted">{w("timeline.nothing")}</p>
        {undatedCount > 0 && <p className="av-muted">{w("timeline.activity.undated_note", { count: undatedCount })}</p>}
      </section>
    );
  }

  const summarySubline =
    even || !busiest
      ? w("timeline.activity.summary_even")
      : w("timeline.activity.summary_busiest", { label: busiest.label, count: busiest.total });

  return (
    <section className="av-understand-activity" data-testid="timeline-activity" aria-labelledby="timeline-activity-heading">
      <h3 id="timeline-activity-heading" className="av-visually-hidden">{w("timeline.view.activity")}</h3>

      <div className="av-understand-activity__summary">
        <p className="av-understand-activity__headline">{w("timeline.activity.summary_count", { count: totalDated })}</p>
        <p className="av-muted">{summarySubline}</p>
        {undatedCount > 0 && <p className="av-muted">{w("timeline.activity.undated_note", { count: undatedCount })}</p>}
      </div>

      <ActivityDensityStrip
        buckets={buckets}
        windowFrom={wf}
        windowTo={wt}
        lanes={data.lanes}
        maxTotal={maxTotal}
        selected={selected}
        onPickBucket={pickBucket}
      />

      {breakdownSegments.length > 0 && (
        <details className="av-understand-activity__breakdown">
          <summary>{w("timeline.activity.breakdown_toggle")}</summary>
          <StackedBar
            vertical
            label={w("timeline.activity.breakdown_label")}
            segments={breakdownSegments}
          />
        </details>
      )}

      <div className="av-understand-activity__list" data-testid="timeline-activity-list">
        {selected && (
          <div className="av-understand-activity__selection-head">
            <h4>{w("timeline.activity.selected", { from: longDate(selected.from), to: longDate(selected.to) })}</h4>
            <button type="button" className="av-understand-time-window__clear" onClick={clearSelection}>
              {w("timeline.activity.clear_selection")}
            </button>
          </div>
        )}
        <ChronologyList caseId={caseId} entries={listEntries} numbers={numbers} headingId="timeline-activity-list-heading" />
        <button type="button" className="av-btn av-btn--secondary" onClick={onOpenChronology}>
          {w("timeline.activity.read_chronology")}
        </button>
      </div>
    </section>
  );
}
