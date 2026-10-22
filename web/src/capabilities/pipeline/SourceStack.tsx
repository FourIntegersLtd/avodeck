// The bundle drawn as one tall stack of pages, cut into bands for each source, with a label beside each
// band. Colours and words come from the caller (the case's pack). Drawn as SVG because the CSP forbids
// inline styles; a hidden list gives screen readers the same labels and counts.
import { useId } from "react";

export interface StackRow {
  key: string;
  label: string;
  pages: number;
  colour: string;
}

const HEIGHT = 440;
const WIDTH = 150;
const MIN_BAND = 14;
const SHORT_BAND = 48;
// Text lines are 17px type at 1.5 line height; the short labels sit 4px apart.
const TEXT_LINE = 25.5;
const SHORT_GAP = 4;
// Where a label's baseline falls in its line.
const BASELINE = 18.75;
const LABEL_X = 174;

/** Band heights that add up to the stack's height, with every band tall enough to see. */
export function bandHeights(pages: number[]): number[] {
  const total = pages.reduce((sum, n) => sum + Math.max(0, n), 0);
  if (total <= 0) return pages.map(() => 0);
  const raw = pages.map((n) => (Math.max(0, n) / total) * HEIGHT);
  const small = raw.map((h) => h < MIN_BAND);
  const roomy = raw.filter((_, i) => !small[i]).reduce((sum, h) => sum + h, 0);
  const scale = roomy > 0 ? (HEIGHT - small.filter(Boolean).length * MIN_BAND) / roomy : 1;
  return raw.map((h, i) => (small[i] ? MIN_BAND : h * scale));
}

interface Props {
  rows: StackRow[];
  /** What the picture says to assistive technology. */
  label: string;
  /** "pages", "page": the unit after a count. */
  unit: (pages: number) => string;
  /** A count written in the person's locale. */
  format: (pages: number) => string;
}

export function SourceStack({ rows, label, unit, format }: Props) {
  const id = useId();
  const clip = `${id}-clip`;
  const heights = bandHeights(rows.map((r) => r.pages));
  const tops = heights.map((_, i) => heights.slice(0, i).reduce((sum, h) => sum + h, 0));
  const compact = rows.filter((_, i) => heights[i]! < SHORT_BAND);
  // The short bands share one block of labels, centred on the space left under the tall bands.
  const regionTop = compact.length > 0 ? tops[heights.findIndex((h) => h < SHORT_BAND)]! : HEIGHT;
  const blockHeight = compact.length * TEXT_LINE + Math.max(0, compact.length - 1) * SHORT_GAP;
  const blockTop = regionTop + (HEIGHT - regionTop) / 2 - blockHeight / 2;
  const lines = compact.map((r, i) => ({ r, y: blockTop + i * (TEXT_LINE + SHORT_GAP) + BASELINE }));
  return (
    <div className="pl-stack">
      <svg className="pl-stack__drawing" viewBox={`0 0 370 ${HEIGHT + 2}`} role="img" aria-label={label}>
        <defs>
          <clipPath id={clip}>
            <rect x="1" y="1" width={WIDTH} height={HEIGHT} rx="9" />
          </clipPath>
        </defs>
        <rect className="pl-stack__shadow" x="12" y="12" width={WIDTH} height={HEIGHT} rx="10" />
        <rect className="pl-stack__page" x="6.5" y="6.5" width={WIDTH + 1} height={HEIGHT + 1} rx="10" />
        <g clipPath={`url(#${clip})`}>
          {rows.map((r, i) => (
            <rect key={r.key} x="1" y={tops[i]! + 1} width={WIDTH} height={heights[i]} fill={r.colour} />
          ))}
        </g>
        <rect className="pl-stack__edge" x="0.5" y="0.5" width={WIDTH + 1} height={HEIGHT + 1} rx="10" />
        {rows.map((r, i) =>
          heights[i]! >= SHORT_BAND ? (
            <g key={r.key} aria-hidden="true">
              <text className="pl-stack__name" x={LABEL_X} y={tops[i]! + heights[i]! / 2 - TEXT_LINE + BASELINE}>{r.label}</text>
              <text className="pl-stack__count" x={LABEL_X} y={tops[i]! + heights[i]! / 2 + BASELINE}>{`${format(r.pages)} ${unit(r.pages)}`}</text>
            </g>
          ) : null,
        )}
        {lines.map(({ r, y }) => (
          <text key={r.key} className="pl-stack__name pl-stack__name--short" x={LABEL_X} y={y} aria-hidden="true">
            {r.label}
            <tspan className="pl-stack__count" dx="8">{format(r.pages)}</tspan>
          </text>
        ))}
      </svg>
      <ul className="av-visually-hidden" aria-label={label}>
        {rows.map((r) => (
          <li key={r.key}>
            {r.label} <span className="av-mono">{format(r.pages)}</span> {unit(r.pages)}
          </li>
        ))}
      </ul>
    </div>
  );
}
