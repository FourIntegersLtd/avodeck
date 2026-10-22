import type { Schemas } from "../../foundation/platform-client";
import { bucketSpanFraction, sparseAxisTicks, type ActivityBucket } from "./activityBuckets";
import { w } from "./words";

type Lane = Schemas["Timeline"]["lanes"][number];

const STRIP_W = 1000;
const STRIP_H = 64;
const PAD = 2;

export function ActivityDensityStrip({
  buckets,
  windowFrom,
  windowTo,
  lanes,
  maxTotal,
  selected,
  onPickBucket,
}: {
  buckets: ActivityBucket[];
  windowFrom: string;
  windowTo: string;
  lanes: Lane[];
  maxTotal: number;
  selected: Pick<ActivityBucket, "from" | "to"> | null;
  onPickBucket: (bucket: ActivityBucket) => void;
}) {
  const ticks = sparseAxisTicks(windowFrom, windowTo, buckets);
  const laneOrder = lanes.map((l) => l.key);
  const laneColour = (key: string) => lanes.find((l) => l.key === key)?.colour ?? "var(--ink-soft)";

  return (
    <div className="av-understand-activity__strip-wrap" data-testid="activity-density-strip">
      <svg
        className="av-understand-activity__strip-svg"
        viewBox={`0 0 ${STRIP_W} ${STRIP_H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={w("timeline.activity.chart_label")}
      >
        {selected &&
          buckets
            .filter((b) => b.from === selected.from && b.to === selected.to)
            .map((b) => {
              const { left, width } = bucketSpanFraction(b.from, b.to, windowFrom, windowTo);
              const x = left * STRIP_W;
              const wPx = width * STRIP_W;
              return (
                <rect
                  key={`sel-${b.key}`}
                  className="av-understand-activity__strip-selection"
                  x={x}
                  y={0}
                  width={wPx}
                  height={STRIP_H}
                />
              );
            })}
        {buckets.map((bucket) => {
          const { left, width } = bucketSpanFraction(bucket.from, bucket.to, windowFrom, windowTo);
          const x = left * STRIP_W + PAD;
          const wPx = Math.max(2, width * STRIP_W - PAD * 2);
          const barH = maxTotal > 0 ? (bucket.total / maxTotal) * (STRIP_H - 4) : 0;
          let stackY = STRIP_H;
          const laneSum = laneOrder.reduce((n, k) => n + (bucket.byLane[k] ?? 0), 0);
          const unplaced = Math.max(0, bucket.total - laneSum);
          const segments: { key: string; count: number; fill: string }[] = [];
          for (const key of laneOrder) {
            const count = bucket.byLane[key] ?? 0;
            if (count > 0) segments.push({ key, count, fill: laneColour(key) });
          }
          if (unplaced > 0) segments.push({ key: "__unplaced__", count: unplaced, fill: "var(--line)" });
          return (
            <g key={bucket.key}>
              {segments.map((seg) => {
                const segH = bucket.total > 0 ? (seg.count / bucket.total) * barH : 0;
                stackY -= segH;
                return (
                  <rect
                    key={seg.key}
                    x={x}
                    y={stackY}
                    width={wPx}
                    height={segH}
                    fill={seg.fill}
                    rx={2}
                  >
                    <title>{w("timeline.activity.bucket_label", { label: bucket.label, count: bucket.total })}</title>
                  </rect>
                );
              })}
            </g>
          );
        })}
      </svg>
      <div className="av-understand-activity__strip-hits" aria-hidden="false">
        {buckets.map((bucket) => {
          const { left, width } = bucketSpanFraction(bucket.from, bucket.to, windowFrom, windowTo);
          const pressed = selected?.from === bucket.from && selected?.to === bucket.to;
          return (
            <button
              key={bucket.key}
              type="button"
              className="av-understand-activity__strip-hit"
              style={{ left: `${left * 100}%`, width: `${width * 100}%` }}
              aria-pressed={pressed}
              aria-label={w("timeline.activity.bucket_label", { label: bucket.label, count: bucket.total })}
              onClick={() => onPickBucket(bucket)}
            />
          );
        })}
      </div>
      <div className="av-understand-activity__strip-axis" aria-hidden="true">
        {ticks.map((tick) => (
          <span key={`${tick.position}-${tick.label}`} className="av-understand-activity__strip-tick" style={{ left: `${tick.position * 100}%` }}>
            {tick.label}
          </span>
        ))}
      </div>
    </div>
  );
}
