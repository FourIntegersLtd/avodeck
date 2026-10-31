import { useCallback, useEffect, useRef, useState } from "react";

/** For forms, selects and checkboxes: `run(fn)` ignores calls while one is running,
 * is busy until the promise settles (even on error) and returns its result. */
export function useBusy() {
  const [busy, setBusy] = useState(false);
  const running = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    if (running.current) return undefined;
    running.current = true;
    setBusy(true);
    try {
      return await fn();
    } finally {
      running.current = false;
      if (mounted.current) setBusy(false);
    }
  }, []);
  return { busy, run };
}
