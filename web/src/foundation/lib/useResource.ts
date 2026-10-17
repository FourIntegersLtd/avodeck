import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../platform-client";

export interface Resource<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  reload: () => void;
}

/** Loads something from the API when `key` changes. */
export function useResource<T>(load: () => Promise<T>, key: unknown[]): Resource<T> {
  const [state, setState] = useState<{ data?: T; error?: ApiError; loading: boolean }>({ loading: true });
  const [version, setVersion] = useState(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(load, key);
  useEffect(() => {
    let live = true;
    setState((s) => ({ ...s, loading: true }));
    run().then(
      (data) => live && setState({ data, loading: false }),
      (error: unknown) => live && setState({ error: error instanceof ApiError ? error : new ApiError(0, "server_error"), loading: false }),
    );
    return () => {
      live = false;
    };
  }, [run, version]);
  return { data: state.data, error: state.error, loading: state.loading, reload: () => setVersion((v) => v + 1) };
}
