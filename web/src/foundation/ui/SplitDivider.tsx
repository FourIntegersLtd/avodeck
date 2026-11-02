// A draggable border between two side-by-side parts of a screen. Drag it, or use the arrow keys (Shift for a
// bigger step, Home and End for the ends), to give either side more room. The value is the left part's width in
// percent of `areaRef`.
import { useEffect, useRef, type RefObject } from "react";
import { t } from "../i18n";

const STEP = 2;
const BIG_STEP = 10;

export function SplitDivider({
  areaRef,
  value,
  min,
  max,
  label,
  onChange,
  onCommit,
  className,
}: {
  areaRef: RefObject<HTMLElement | null>;
  value: number;
  min: number;
  max: number;
  label: string;
  onChange: (pct: number) => void;
  /** The width the person settled on: after a key press, and when a drag ends or is cancelled. */
  onCommit?: (pct: number) => void;
  /** Extra class, for a screen that hides the divider on a phone. */
  className?: string;
}) {
  const clamp = (pct: number) => (Number.isFinite(pct) ? Math.min(max, Math.max(min, pct)) : min);
  const latest = useRef(value);
  latest.current = value;
  const drag = useRef<{ stop: () => void } | null>(null);
  // Leaving the screen mid-drag must not leave listeners on the document.
  useEffect(() => () => drag.current?.stop(), []);

  function set(pct: number) {
    const next = clamp(pct);
    latest.current = next;
    onChange(next);
    onCommit?.(next);
  }

  function onPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    const area = areaRef.current;
    if (!area) return;
    e.preventDefault();
    drag.current?.stop();
    const grip = e.currentTarget;
    grip.setPointerCapture?.(e.pointerId);
    const width = area.getBoundingClientRect().width || 1;
    const startX = e.clientX;
    const start = latest.current;
    const onMove = (ev: PointerEvent) => {
      const next = clamp(start + ((ev.clientX - startX) / width) * 100);
      latest.current = next;
      onChange(next);
    };
    const stop = () => {
      grip.releasePointerCapture?.(e.pointerId);
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onEnd);
      document.removeEventListener("pointercancel", onEnd);
      drag.current = null;
    };
    const onEnd = () => {
      stop();
      onCommit?.(latest.current);
    };
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onEnd);
    document.addEventListener("pointercancel", onEnd);
    drag.current = { stop };
  }

  return (
    <div
      className={className ? `av-split ${className}` : "av-split"}
      role="separator"
      aria-orientation="vertical"
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={Math.round(value)}
      aria-valuetext={t("ui.split.value", { percent: Math.round(value) })}
      aria-label={label}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={(e) => {
        const step = e.shiftKey ? BIG_STEP : STEP;
        if (e.key === "ArrowLeft") set(value - step);
        else if (e.key === "ArrowRight") set(value + step);
        else if (e.key === "Home") set(min);
        else if (e.key === "End") set(max);
        else return;
        e.preventDefault();
      }}
    />
  );
}
