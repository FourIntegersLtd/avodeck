// A draggable border between two side-by-side parts of a screen. Drag it, or use the arrow keys, to give
// either side more room. The value is the left part's width in percent of `areaRef`.
import { useRef, type RefObject } from "react";

export function SplitDivider({
  areaRef,
  value,
  min,
  max,
  label,
  onChange,
}: {
  areaRef: RefObject<HTMLElement | null>;
  value: number;
  min: number;
  max: number;
  label: string;
  onChange: (pct: number) => void;
}) {
  const clamp = (pct: number) => Math.min(max, Math.max(min, pct));
  const latest = useRef(value);
  latest.current = value;

  function onPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    const area = areaRef.current;
    if (!area) return;
    e.preventDefault();
    const grip = e.currentTarget;
    grip.setPointerCapture?.(e.pointerId);
    const width = area.getBoundingClientRect().width || 1;
    const startX = e.clientX;
    const start = latest.current;
    const onMove = (ev: PointerEvent) => onChange(clamp(start + ((ev.clientX - startX) / width) * 100));
    const onUp = () => {
      grip.releasePointerCapture?.(e.pointerId);
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
    };
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
  }

  return (
    <div
      className="av-split"
      role="separator"
      aria-orientation="vertical"
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={Math.round(value)}
      aria-label={label}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={(e) => {
        const step = e.key === "ArrowLeft" ? -2 : e.key === "ArrowRight" ? 2 : 0;
        if (!step) return;
        e.preventDefault();
        onChange(clamp(value + step));
      }}
    />
  );
}
