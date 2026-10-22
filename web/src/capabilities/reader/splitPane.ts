/** How wide the scan column is on S09 (percent of the work row). Remembered for this browser tab. */
export const SPLIT_STORAGE_KEY = "avodeck.reader.split";
export const DEFAULT_SPLIT_PCT = 70;
export const MIN_SPLIT_PCT = 32;
export const MAX_SPLIT_PCT = 78;

export function clampSplit(pct: number): number {
  if (!Number.isFinite(pct)) return DEFAULT_SPLIT_PCT;
  return Math.min(MAX_SPLIT_PCT, Math.max(MIN_SPLIT_PCT, pct));
}

export function loadSplitPct(): number {
  return DEFAULT_SPLIT_PCT;
}

export function saveSplitPct(_pct: number): void {
  /* Split width is held in React state only (web/AGENTS.md: no browser storage). */
}
