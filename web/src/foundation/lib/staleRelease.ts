const RELOAD_MARK = "avNewReleaseReloadAt";
const RELOAD_WINDOW_MS = 30_000;

/** True when a screen file could not be downloaded: after a new release the old files are gone, so a tab
 * opened before it asks for files that no longer exist. Wording differs per browser and for Vite's CSS. */
export function isMissingScreenFile(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? "");
  return /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Unable to preload CSS/i.test(
    message,
  );
}

/** Reload once to pick up the new release. The time of the reload is marked on this page's history entry
 * (nothing is stored in the browser), which survives the reload. If it happens again within 30 seconds,
 * return false so the caller shows an error instead of reloading in a loop. */
export function reloadOnceForNewRelease(reload: () => void = () => window.location.reload()): boolean {
  try {
    const state = (window.history.state ?? {}) as Record<string, unknown>;
    if (Date.now() - Number(state[RELOAD_MARK] ?? 0) < RELOAD_WINDOW_MS) return false;
    window.history.replaceState({ ...state, [RELOAD_MARK]: Date.now() }, "");
  } catch {
    return false;
  }
  reload();
  return true;
}
