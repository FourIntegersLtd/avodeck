// Draggable border between the page scan and the suggestions column (S09).
import { useRef, type RefObject } from "react";
import { clampSplit, MAX_SPLIT_PCT, MIN_SPLIT_PCT } from "./splitPane";
import { w } from "./words";

export function SplitDivider({
  workRef,
  value,
  onChange,
  onCommit,
}: {
  workRef: RefObject<HTMLElement | null>;
  value: number;
  onChange: (pct: number) => void;
  onCommit: (pct: number) => void;
}) {
  const latest = useRef(value);
  latest.current = value;

  function nudge(delta: number) {
    const next = clampSplit(value + delta);
    onChange(next);
    onCommit(next);
  }

  function onPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    const work = workRef.current;
    if (!work) return;
    e.preventDefault();
    const grip = e.currentTarget;
    grip.setPointerCapture(e.pointerId);
    const width = work.getBoundingClientRect().width;
    const startX = e.clientX;
    const start = value;

    const onMove = (ev: PointerEvent) => {
      const next = clampSplit(start + ((ev.clientX - startX) / width) * 100);
      latest.current = next;
      onChange(next);
    };
    const onUp = () => {
      grip.releasePointerCapture(e.pointerId);
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
      onCommit(latest.current);
    };
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
  }

  return (
    <div
      className="rd-split"
      role="separator"
      aria-orientation="vertical"
      aria-valuemin={MIN_SPLIT_PCT}
      aria-valuemax={MAX_SPLIT_PCT}
      aria-valuenow={Math.round(value)}
      aria-label={w("read.resize_split")}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft") {
          e.preventDefault();
          nudge(-2);
        } else if (e.key === "ArrowRight") {
          e.preventDefault();
          nudge(2);
        }
      }}
    />
  );
}
