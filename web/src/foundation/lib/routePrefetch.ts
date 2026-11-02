let prefetcher: ((to: string) => void) | null = null;

/** The app registers how to start a screen's download for a path (app/routes.tsx). */
export function setRoutePrefetcher(fn: ((to: string) => void) | null) {
  prefetcher = fn;
}

/** Start downloading the screen behind `to` early (nav link hover, focus and touch). Never throws. */
export function prefetchRoute(to: string) {
  try {
    prefetcher?.(to);
  } catch {
    /* a failed warm-up must never break the click that follows */
  }
}
