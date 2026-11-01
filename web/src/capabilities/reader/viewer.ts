// The page viewer's arithmetic (screen 09). Display only: turning, fitting and "Make it clearer" change how
// the page is shown on this screen, never the page, its words or their boxes (the server keeps those).

import { VIEW_DEFAULTS, ZOOM_MAX, ZOOM_MIN, turnBy, type View } from "../../foundation/ui";

export type Box = { x0: number; y0: number; x1: number; y1: number };
export { clampZoom, drawnSize, turnBy, VIEW_DEFAULTS, ZOOM_MAX, ZOOM_MIN } from "../../foundation/ui";
export type { Fit, View } from "../../foundation/ui";

/** Where a box on the upright page lands once the page is turned clockwise (fractions of the turned page). */
export function turnedBox(b: Box, turn: number): Box {
  switch (turnBy(turn, 0)) {
    case 90:
      return { x0: 1 - b.y1, y0: b.x0, x1: 1 - b.y0, y1: b.x1 };
    case 180:
      return { x0: 1 - b.x1, y0: 1 - b.y1, x1: 1 - b.x0, y1: 1 - b.y0 };
    case 270:
      return { x0: b.y0, y0: 1 - b.x1, x1: b.y1, y1: 1 - b.x0 };
    default:
      return b;
  }
}

/** The items of a long strip to draw: those in view plus a few either side. */
export function stripWindow({ count, itemSize, scroll, view, overscan }: { count: number; itemSize: number; scroll: number; view: number; overscan: number }) {
  const first = Math.floor(scroll / itemSize);
  const shown = Math.ceil(view / itemSize);
  return { start: Math.max(0, first - overscan), end: Math.min(count, first + shown + overscan) };
}

// Held in this tab's memory: browser storage is off limits in capabilities (the no-restricted-globals guard),
// so the settings last for the visit (every page, leaving and coming back), not past a reload.
let remembered: View = VIEW_DEFAULTS;

function valid(v: unknown): v is View {
  const o = v as Record<string, unknown> | null;
  const n = (x: unknown, lo: number, hi: number) => typeof x === "number" && Number.isFinite(x) && x >= lo && x <= hi;
  return !!o && ["width", "page", "zoom"].includes(o.fit as string) && n(o.zoom, ZOOM_MIN, ZOOM_MAX) && n(o.brightness, 50, 200) && n(o.contrast, 50, 200);
}

/** This viewer's view settings for the visit. */
export function loadView(): View {
  return remembered;
}

export function saveView(view: View) {
  if (valid(view)) remembered = { fit: view.fit, zoom: view.zoom, brightness: view.brightness, contrast: view.contrast };
}

/** Back to the defaults (signing out, or a test). */
export function forgetView() {
  remembered = VIEW_DEFAULTS;
  stripHidden = false;
}

/** Whether the page strip is hidden. Held for the visit (the guard allows no browser storage). */
let stripHidden = false;
export const loadStripHidden = (): boolean => stripHidden;
export function saveStripHidden(hidden: boolean) {
  stripHidden = hidden;
}
