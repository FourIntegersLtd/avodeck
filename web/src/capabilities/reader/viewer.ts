// The page viewer's arithmetic (screen 09). Display only: turning, fitting and "Make it clearer" change how
// the page is shown on this screen, never the page, its words or their boxes (the server keeps those).

export type Box = { x0: number; y0: number; x1: number; y1: number };
export type Fit = "width" | "page" | "zoom";
export interface View {
  fit: Fit;
  /** Share of the space's width, in percent (used when `fit` is "zoom"). */
  zoom: number;
  brightness: number;
  contrast: number;
}

export const VIEW_DEFAULTS: View = { fit: "width", zoom: 100, brightness: 100, contrast: 100 };
export const ZOOM_MIN = 50;
export const ZOOM_MAX = 400;

/** A quarter turn added to a turn, clockwise, kept in 0..270. */
export function turnBy(turn: number, by: number): number {
  return (((turn + by) % 360) + 360) % 360;
}

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

export function clampZoom(zoom: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(zoom)));
}

type Size = { width: number; height: number };

/** The page's size on screen once turned: fit to the width, the whole page in view, or a zoom of the width. */
export function drawnSize(page: Size, space: Size, turn: number, view: Pick<View, "fit" | "zoom">): Size {
  const sideways = turnBy(turn, 0) % 180 !== 0;
  const [across, down] = sideways ? [page.height, page.width] : [page.width, page.height];
  let width = space.width;
  if (view.fit === "page" && space.height > 0) width = Math.min(space.width, (space.height * across) / down);
  if (view.fit === "zoom") width = (space.width * view.zoom) / 100;
  return { width, height: (width * down) / across };
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
}
