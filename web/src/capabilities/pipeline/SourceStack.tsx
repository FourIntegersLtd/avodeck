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
const LINE = 26;

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

const TALL_BLOCK = 44; // a name line over a count line
const SHORT_BLOCK = LINE; // name and count on one line

export interface LabelBlock {
  top: number;
  height: number;
  /** Two lines (name over count) for a band tall enough; otherwise one. */
  tall: boolean;
}

/** Where each band's label sits: centred on its band, then nudged apart so no two labels overlap and none
 * leaves the drawing. One pass pushes labels down out of each other's way; a second pushes them back up when
 * the last one would run off the bottom. */
export function labelBlocks(heights: number[]): LabelBlock[] {
  const blocks = heights.map((h, i) => {
    const tall = h >= SHORT_BAND;
    const height = tall ? TALL_BLOCK : SHORT_BLOCK;
    const centre = heights.slice(0, i).reduce((sum, x) => sum + x, 0) + h / 2;
    return { top: centre - height / 2, height, tall };
  });
  for (let i = 0; i < blocks.length; i += 1) {
    const floor = i === 0 ? 0 : blocks[i - 1]!.top + blocks[i - 1]!.height;
    blocks[i]!.top = Math.max(blocks[i]!.top, floor);
  }
  for (let i = blocks.length - 1; i >= 0; i -= 1) {
    const ceiling = i === blocks.length - 1 ? HEIGHT : blocks[i + 1]!.top;
    blocks[i]!.top = Math.max(0, Math.min(blocks[i]!.top, ceiling - blocks[i]!.height));
  }
  return blocks;
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
  const blocks = labelBlocks(heights);
  return (
    <div className="pl-stack">
      <svg className="pl-stack__drawing" viewBox="0 0 370 452" role="img" aria-label={label}>
        <defs>
          <clipPath id={clip}>
            <rect x="0" y="0" width={WIDTH} height={HEIGHT} rx="10" />
          </clipPath>
        </defs>
        <rect className="pl-stack__shadow" x="12" y="12" width={WIDTH} height={HEIGHT} rx="10" />
        <rect className="pl-stack__page" x="6" y="6" width={WIDTH} height={HEIGHT} rx="10" />
        <g clipPath={`url(#${clip})`}>
          {rows.map((r, i) => (
            <rect key={r.key} x="0" y={tops[i]} width={WIDTH} height={heights[i]} fill={r.colour} />
          ))}
        </g>
        <rect className="pl-stack__edge" x="0.5" y="0.5" width={WIDTH - 1} height={HEIGHT - 1} rx="10" />
        {rows.map((r, i) => {
          const b = blocks[i]!;
          return b.tall ? (
            <g key={r.key} aria-hidden="true">
              <text className="pl-stack__name" x="184" y={b.top + 16}>{r.label}</text>
              <text className="pl-stack__count" x="184" y={b.top + 36}>{`${format(r.pages)} ${unit(r.pages)}`}</text>
            </g>
          ) : (
            <text key={r.key} className="pl-stack__name pl-stack__name--short" x="184" y={b.top + 18} aria-hidden="true">
              {r.label}
              <tspan className="pl-stack__count" dx="8">{format(r.pages)}</tspan>
            </text>
          );
        })}
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
