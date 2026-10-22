// S11 ribbon: the whole record in one strip, a row per lane, with a framed window over the stretch the board shows.
// The window's ends are sliders (arrow keys move a month, Page Up and Page Down a year); dragging an end or the frame
// moves it, and a click elsewhere centres it there. The board changes once, when the move ends.
import { useRef, useState } from "react";
import { longDate } from "./shared";
import { useHighlightAttrs } from "./highlight";
import type { RibbonData } from "./ribbonData";
import { dayMs } from "./storyLayout";
import { w } from "./words";

const W = 1000;
const ROW = 12;

function iso(ms: number): string {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
}

function shiftMonths(day: string, months: number, end: boolean): string {
  const [y, m] = day.split("-").map(Number);
  const first = Date.UTC(y!, m! - 1 + months, 1);
  // A window's end sits on the last day of its month, its start on the first.
  return end ? iso(Date.UTC(y!, m! + months, 0)) : iso(first);
}

type Drag = { kind: "start" | "end" | "move"; x0: number; from0: number; to0: number };

export function TimelineRibbon({
  data,
  from,
  to,
  onChange,
}: {
  data: RibbonData;
  from: string;
  to: string;
  onChange: (from: string, to: string) => void;
}) {
  const track = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [preview, setPreview] = useState<{ from: number; to: number } | null>(null);
  const { range, lanes, months } = data;
  if (!range) return null;

  // The strip covers the record and the window, whichever reaches further.
  const first = from && from < range.first ? from : range.first;
  const last = to && to > range.last ? to : range.last;
  const start = dayMs(`${first.slice(0, 7)}-01`);
  const [ly, lm] = last.split("-").map(Number);
  const end = Date.UTC(ly!, lm!, 0);
  const span = Math.max(end - start, 86_400_000);
  const clamp = (ms: number) => Math.min(end, Math.max(start, ms));
  const lensFrom = preview?.from ?? clamp(from ? dayMs(from) : start);
  const lensTo = preview?.to ?? clamp(to ? dayMs(to) : end);
  const pct = (ms: number) => ((ms - start) / span) * 100;
  const height = lanes.length * ROW + 8;
  // Each month's bar sits at its own dates, so the strip lines up with the window whatever its reach.
  const monthAt = months.map((m) => {
    const [y, mo] = m.split("-").map(Number);
    const a = Date.UTC(y!, mo! - 1, 1);
    const b = Date.UTC(y!, mo!, 1);
    return { x: ((a - start) / span) * W, w: Math.max(1, ((b - a) / span) * W - 0.6) };
  });

  const commit = (f: number, t: number) => onChange(iso(clamp(Math.min(f, t))), iso(clamp(Math.max(f, t))));
  const atPointer = (clientX: number) => {
    const box = track.current!.getBoundingClientRect();
    return start + ((clientX - box.left) / Math.max(1, box.width)) * span;
  };

  const keys = (which: "start" | "end") => (e: React.KeyboardEvent) => {
    const step: Record<string, number> = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1, PageUp: 12, PageDown: -12 };
    const current = iso(which === "start" ? lensFrom : lensTo);
    let next: string | null = null;
    if (e.key in step) next = shiftMonths(current, step[e.key]!, which === "end");
    if (e.key === "Home") next = iso(start);
    if (e.key === "End") next = iso(end);
    if (!next) return;
    e.preventDefault();
    if (which === "start") commit(Math.min(dayMs(next), lensTo - 86_400_000), lensTo);
    else commit(lensFrom, Math.max(dayMs(next), lensFrom + 86_400_000));
  };

  const down = (kind: Drag["kind"]) => (e: React.PointerEvent) => {
    e.stopPropagation();
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    setDrag({ kind, x0: atPointer(e.clientX), from0: lensFrom, to0: lensTo });
  };
  const move = (e: React.PointerEvent) => {
    if (!drag) return;
    const d = atPointer(e.clientX) - drag.x0;
    if (drag.kind === "move") {
      const width = drag.to0 - drag.from0;
      const f = clamp(Math.min(drag.from0 + d, end - width));
      setPreview({ from: f, to: f + width });
    } else if (drag.kind === "start") setPreview({ from: clamp(Math.min(drag.from0 + d, drag.to0 - 86_400_000)), to: drag.to0 });
    else setPreview({ from: drag.from0, to: clamp(Math.max(drag.to0 + d, drag.from0 + 86_400_000)) });
  };
  const up = () => {
    if (drag && preview) commit(preview.from, preview.to);
    setDrag(null);
    setPreview(null);
  };
  const centre = (e: React.PointerEvent) => {
    const width = lensTo - lensFrom;
    const f = clamp(Math.min(atPointer(e.clientX) - width / 2, end - width));
    commit(f, f + width);
  };

  const years: number[] = [];
  const y0 = new Date(start).getUTCFullYear();
  const y1 = new Date(end).getUTCFullYear();
  const every = Math.max(1, Math.ceil((y1 - y0 + 1) / 8));
  for (let y = y0; y <= y1; y += every) years.push(y);

  return (
    <div className="av-story-ribbon" data-testid="timeline-ribbon">
      <p className="av-story-ribbon__hint">
        <strong>{w("timeline.ribbon")}</strong> · {w("timeline.ribbon_hint")}
      </p>
      <div className="av-story-ribbon__track" ref={track} onPointerDown={centre} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
        <svg viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" aria-hidden="true" focusable="false" style={{ height }}>
          {lanes.map((lane, i) => {
            const max = Math.max(1, ...lane.counts);
            const y = 4 + i * ROW + ROW - 2;
            const d = lane.counts
              .map((c, m) => {
                const at = monthAt[m];
                if (!c || !at) return "";
                const h = Math.max(1.5, (c / max) * (ROW - 3));
                return `M${at.x.toFixed(1)} ${y}v-${h.toFixed(1)}h${at.w.toFixed(1)}v${h.toFixed(1)}z`;
              })
              .join("");
            return <path key={lane.key} d={d} fill={lane.colour ?? "var(--ink-soft)"} />;
          })}
        </svg>
        {data.pins.map((pin) => (
          <RibbonPin key={pin.fact_id} pin={pin} left={pct(dayMs(pin.date))} />
        ))}
        <div className="av-story-lens" style={{ left: `${pct(lensFrom)}%`, width: `${Math.max(0.5, pct(lensTo) - pct(lensFrom))}%` }} onPointerDown={down("move")}>
          <span
            className="av-story-lens__end av-story-lens__end--start"
            role="slider"
            tabIndex={0}
            aria-label={w("timeline.lens_start")}
            aria-valuemin={0}
            aria-valuemax={Math.round(span / 86_400_000)}
            aria-valuenow={Math.round((lensFrom - start) / 86_400_000)}
            aria-valuetext={longDate(iso(lensFrom))}
            onKeyDown={keys("start")}
            onPointerDown={down("start")}
          />
          <span
            className="av-story-lens__end av-story-lens__end--end"
            role="slider"
            tabIndex={0}
            aria-label={w("timeline.lens_end")}
            aria-valuemin={0}
            aria-valuemax={Math.round(span / 86_400_000)}
            aria-valuenow={Math.round((lensTo - start) / 86_400_000)}
            aria-valuetext={longDate(iso(lensTo))}
            onKeyDown={keys("end")}
            onPointerDown={down("end")}
          />
        </div>
      </div>
      <ul className="av-story-ribbon__years" aria-label={w("timeline.years")}>
        {years.map((y) => (
          <li key={y} style={{ left: `${pct(Date.UTC(y, 0, 1)) < 0 ? 0 : pct(Date.UTC(y, 0, 1))}%` }}>
            {y}
          </li>
        ))}
      </ul>
    </div>
  );
}

function RibbonPin({ pin, left }: { pin: RibbonData["pins"][number]; left: number }) {
  const highlight = useHighlightAttrs(pin.question_ids);
  return <span className="av-story-pin" data-state={pin.has_finding ? "look" : "linked"} style={{ left: `${left}%` }} aria-hidden="true" {...highlight} />;
}
