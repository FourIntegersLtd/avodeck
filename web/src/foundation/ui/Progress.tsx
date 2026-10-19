import type { ReactNode } from "react";

// Drawn with SVG attributes only: the content security policy forbids inline styles.

const fraction = (value: number, max: number) => (max > 0 ? Math.min(1, Math.max(0, value / max)) : 0);

export interface ProgressRingProps {
  value: number;
  max: number;
  /** What the ring says to assistive technology, e.g. "3 of 5 done". */
  label: string;
  size?: number;
  /** Words or a number shown in the middle. */
  children?: ReactNode;
}

const STROKE = 9;

/** A ring that fills clockwise from the top. */
export function ProgressRing({ value, max, label, size = 80, children }: ProgressRingProps) {
  const radius = (size - STROKE) / 2;
  const circumference = 2 * Math.PI * radius;
  const filled = circumference * fraction(value, max);
  const mid = size / 2;
  return (
    <span className="av-ring av-ring--drawn">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={label}>
        <circle className="av-ring__track" cx={mid} cy={mid} r={radius} fill="none" strokeWidth={STROKE} />
        <circle
          className="av-ring__arc"
          cx={mid}
          cy={mid}
          r={radius}
          fill="none"
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={`${filled} ${circumference - filled}`}
          transform={`rotate(-90 ${mid} ${mid})`}
        />
      </svg>
      {children && <span className="av-ring__label">{children}</span>}
    </span>
  );
}

export interface StackedBarSegment {
  key: string;
  value: number;
  /** The fill colour; it comes from the caller (a pack's source colours), never from this file. */
  color: string;
  label: string;
}

/** One bar split into parts, each as wide as its share of the total, with a legend. */
export function StackedBar({ segments, label }: { segments: StackedBarSegment[]; label: string }) {
  const total = segments.reduce((sum, s) => sum + Math.max(0, s.value), 0);
  let x = 0;
  return (
    <div className="av-stacked">
      <svg className="av-stacked__bar" viewBox="0 0 100 12" preserveAspectRatio="none" role="img" aria-label={label}>
        {segments.map((s) => {
          const width = total > 0 ? (Math.max(0, s.value) / total) * 100 : 0;
          const rect = (
            <rect key={s.key} x={x} y={0} width={width} height={12} fill={s.color}>
              <title>{`${s.label}: ${s.value}`}</title>
            </rect>
          );
          x += width;
          return rect;
        })}
      </svg>
      <ul className="av-stacked__legend">
        {segments.map((s) => (
          <li key={s.key}>
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" focusable="false">
              <circle cx="6" cy="6" r="6" fill={s.color} />
            </svg>
            <span>{s.label}</span>
            <span className="av-mono">{s.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A 12px pill that fills left to right. */
export function ProgressBar({ value, max, label }: { value: number; max: number; label: string }) {
  return (
    <div className="av-progress" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuenow={value} aria-valuemax={max}>
      <svg viewBox="0 0 100 12" preserveAspectRatio="none" aria-hidden="true" focusable="false">
        <rect className="av-progress__fill" x="0" y="0" width={fraction(value, max) * 100} height="12" />
      </svg>
    </div>
  );
}
