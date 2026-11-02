import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { ApiError } from "../platform-client";
import { cacheGeneration, cacheKey, queryClient } from "./resourceCache";

export interface Resource<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  reload: () => void;
}

export interface ResourceOptions {
  /** A name unique to this call site (guarded by foundation/guards/resourceCache.test.ts). With it, a
   * resource loaded before shows at once and is not fetched again for 30 seconds; without it, nothing
   * is kept. Leave it out when the answer seeds a form's own state (`useState(initial...)`): the fresh
   * answer would not reach the form. */
  cache?: string;
}

/** Loads something from the API when `key` changes. `reload()` always asks the server again. */
export function useResource<T>(load: () => Promise<T>, key: unknown[], options?: ResourceOptions): Resource<T> {
  const own = useId();
  const kept = cacheKey(options?.cache, key);
  const [reloading, setReloading] = useState(false);
  const q = useQuery<T, ApiError>(
    {
      queryKey: kept ?? ["nocache", own, ...key],
      queryFn: () => {
        const startedAt = cacheGeneration();
        return load()
          .then((answer) => {
            // The cache was emptied while this was on its way: show it to this screen but do not keep it.
            if (startedAt !== cacheGeneration() && kept) queueMicrotask(() => queryClient.removeQueries({ queryKey: kept, exact: true }));
            return answer;
          })
          .finally(() => setReloading(false));
      },
      placeholderData: keepPreviousData, // a new key keeps the old answer in view, marked as loading
      ...(kept ? {} : { staleTime: 0, gcTime: 0 }),
    },
    queryClient,
  );
  const failed = q.isError;
  const data = failed ? undefined : q.data;
  const loading = q.isFetching && (data === undefined || q.isPlaceholderData || reloading);
  const error = failed && !loading ? (q.error instanceof ApiError ? q.error : new ApiError(0, "server_error")) : undefined;
  return {
    data,
    error,
    loading,
    reload: () => {
      setReloading(true);
      void q.refetch();
    },
  };
}
