import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../platform-client";
import { cacheGeneration, cacheKey, dropCache, readCache, writeCache } from "./resourceCache";

export interface Resource<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  reload: () => void;
}

export interface ResourceOptions {
  /** A name unique to this call site (guarded by foundation/guards/resourceCache.test.ts). With it, a
   * resource loaded before shows at once while it is fetched again; without it, nothing is kept.
   * Leave it out when the answer seeds a form's own state (`useState(initial...)`): the fresh answer
   * would not reach the form. */
  cache?: string;
}

/** Loads something from the API when `key` changes. `reload()` always asks the server again. */
export function useResource<T>(load: () => Promise<T>, key: unknown[], options?: ResourceOptions): Resource<T> {
  const entry = cacheKey(options?.cache, key);
  const [state, setState] = useState<{ data?: T; error?: ApiError; loading: boolean }>(() => {
    const hit = entry ? readCache<T>(entry) : undefined;
    return hit ? { data: hit.value, loading: false } : { loading: true };
  });
  const [version, setVersion] = useState(0);
  const lastVersion = useRef(version);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(load, key);
  useEffect(() => {
    let live = true;
    const reloading = lastVersion.current !== version;
    lastVersion.current = version;
    const hit = entry && !reloading ? readCache<T>(entry) : undefined;
    setState((s) => (hit ? (s.data === hit.value && !s.loading && !s.error ? s : { data: hit.value, loading: false }) : { ...s, loading: true }));
    const startedAt = cacheGeneration();
    run().then(
      (data) => {
        if (entry) writeCache(entry, data, startedAt);
        if (live) setState({ data, loading: false });
      },
      (error: unknown) => {
        if (entry) dropCache(entry);
        if (live) setState({ error: error instanceof ApiError ? error : new ApiError(0, "server_error"), loading: false });
      },
    );
    return () => {
      live = false;
    };
    // `entry` follows `key`, which already re-creates `run`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, version]);
  return { data: state.data, error: state.error, loading: state.loading, reload: () => setVersion((v) => v + 1) };
}
