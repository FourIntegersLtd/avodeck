// The shared page viewer's arithmetic and state. Display only: turning, fitting and "clearer" change how a
// page is shown on this screen, never the page, its words or their boxes (the server keeps those).
import { useCallback, useRef, useState } from "react";

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
export const ZOOM_STEP = 25;

/** A quarter turn added to a turn, clockwise, kept in 0..270. */
export function turnBy(turn: number, by: number): number {
  return (((turn + by) % 360) + 360) % 360;
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

/** A CSS filter for the picture only. */
export function viewFilter(view: Pick<View, "brightness" | "contrast">): string | undefined {
  if (view.brightness === 100 && view.contrast === 100) return undefined;
  return `brightness(${view.brightness / 100}) contrast(${view.contrast / 100})`;
}

export interface PageViewOptions {
  /** Where the view starts (the owner may keep its own copy and pass it back here). */
  view?: View;
  /** The turn to start at. */
  turn?: number;
  onView?: (view: View) => void;
  onTurn?: (turn: number) => void;
}

export function usePageView(options: PageViewOptions = {}) {
  const { onView, onTurn } = options;
  const [view, setViewState] = useState<View>(options.view ?? VIEW_DEFAULTS);
  const [turn, setTurnState] = useState(options.turn ?? 0);

  // The latest view lives in a ref so the owner is told outside the state updater (which may run twice).
  const viewRef = useRef(view);
  const change = useCallback(
    (fn: (v: View) => View) => {
      const next = fn(viewRef.current);
      viewRef.current = next;
      setViewState(next);
      onView?.(next);
    },
    [onView],
  );
  const setTurn = useCallback(
    (next: number) => {
      setTurnState(next);
      onTurn?.(next);
    },
    [onTurn],
  );

  /** Zoom by `points` from `from`, the share of the width now on screen (defaults to the current zoom). */
  const zoomBy = useCallback((points: number, from?: number) => change((v) => ({ ...v, fit: "zoom", zoom: clampZoom((v.fit === "zoom" ? v.zoom : (from ?? v.zoom)) + points) })), [change]);
  return {
    view,
    turn,
    filter: viewFilter(view),
    zoomBy,
    zoomIn: useCallback((from?: number) => zoomBy(ZOOM_STEP, from), [zoomBy]),
    zoomOut: useCallback((from?: number) => zoomBy(-ZOOM_STEP, from), [zoomBy]),
    setFit: useCallback((fit: Fit) => change((v) => ({ ...v, fit })), [change]),
    setClearer: useCallback((c: Partial<Pick<View, "brightness" | "contrast">>) => change((v) => ({ ...v, ...c })), [change]),
    /** Change any part of the view in one update. */
    setView: useCallback((next: Partial<View>) => change((v) => ({ ...v, ...next })), [change]),
    turnLeft: useCallback(() => setTurn(turnBy(turn, -90)), [setTurn, turn]),
    turnRight: useCallback(() => setTurn(turnBy(turn, 90)), [setTurn, turn]),
    setTurn,
    /** Back to normal: default view and upright. */
    reset: useCallback(() => {
      change(() => VIEW_DEFAULTS);
      setTurn(0);
    }, [change, setTurn]),
  };
}
