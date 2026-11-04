// The live hub: the only place in the app that opens an EventSource. One stream per open case
// (GET /v1/cases/{id}/events) carries the progress of every background job on that case. Every screen
// that waits listens to it through the hooks below, so the browser never uses more than one connection
// per case and never polls. A stream is opened by the first listener and closed by the last.
import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { api, apiOrigin, unwrap } from "../platform-client";

/** What the server says about a job: its state and counts, never any patient text. */
export interface LiveJob {
  id: string;
  kind: string;
  status: string;
  step: string | null;
  pct: number;
  eta_seconds: number | null;
  step_done: number | null;
  step_total: number | null;
  error_code: string | null;
}

interface Entry {
  source: EventSource;
  count: number;
  jobs: LiveJob[] | null;
  listeners: Set<() => void>;
}

const CLOSED = 2; // EventSource.readyState: the browser has given up on this stream
const RELOAD_MS = 2000;
const entries = new Map<string, Entry>();

/** Keep the old object for every job that has not changed, so a screen only redraws for its own job. */
function share(before: LiveJob[] | null, next: LiveJob[]): LiveJob[] {
  const old = new Map((before ?? []).map((j) => [j.id, j]));
  return next.map((j) => {
    const was = old.get(j.id);
    return was && JSON.stringify(was) === JSON.stringify(j) ? was : j;
  });
}

function open(caseId: string): Entry {
  const source = new EventSource(`${apiOrigin()}/v1/cases/${caseId}/events`, { withCredentials: true });
  const entry: Entry = { source, count: 0, jobs: null, listeners: new Set() };
  source.onmessage = (e: MessageEvent<string>) => {
    const data = JSON.parse(e.data) as { jobs: LiveJob[] };
    entry.jobs = share(entry.jobs, data.jobs);
    entry.listeners.forEach((listener) => listener());
  };
  source.onerror = () => {
    // While the browser is retrying by itself there is nothing to do. A stream it has given up on was
    // refused (signed out, or no access): ask the normal way, which sends a signed-out person to sign in.
    if (source.readyState === CLOSED) void unwrap(api.GET("/v1/auth/session")).catch(() => undefined);
  };
  return entry;
}

/** Listen to a case's jobs. Returns the way to stop. */
export function subscribeToCase(caseId: string, listener: () => void): () => void {
  let entry = entries.get(caseId);
  if (!entry) {
    entry = open(caseId);
    entries.set(caseId, entry);
  }
  entry.count += 1;
  entry.listeners.add(listener);
  const mine = entry;
  return () => {
    mine.listeners.delete(listener);
    mine.count -= 1;
    if (mine.count === 0) {
      mine.source.close();
      if (entries.get(caseId) === mine) entries.delete(caseId);
    }
  };
}

/** The latest snapshot of a case's jobs, or null before the first one has arrived. */
export function caseJobsNow(caseId: string): LiveJob[] | null {
  return entries.get(caseId)?.jobs ?? null;
}

/** The case's live jobs: active ones and any that ended in the last few minutes. Null until the first
 * snapshot arrives, and always null for a null case (which opens nothing). */
export function useCaseJobs(caseId: string | null): LiveJob[] | null {
  const subscribe = useCallback((listener: () => void) => (caseId ? subscribeToCase(caseId, listener) : () => undefined), [caseId]);
  return useSyncExternalStore(subscribe, () => (caseId ? caseJobsNow(caseId) : null));
}

/** One job of a case, live. Null while it is not in the snapshot. */
export function useJob(caseId: string | null, jobId: string | null): LiveJob | null {
  const jobs = useCaseJobs(caseId);
  return (jobId && jobs?.find((j) => j.id === jobId)) || null;
}

/** Call `reload` when something on the case that a screen shows may have changed: a job's status
 * changed, a job appeared or went, or (once, first) the stream opened, in case a job ended before it did.
 * Counts moving never trigger it, and it runs at most once every two seconds with the last change kept,
 * so a long job cannot make a screen hammer the API. Counts are read from `useCaseJobs`, not reloaded. */
export function useOnCaseChange(caseId: string | null, reload: () => void): void {
  const jobs = useCaseJobs(caseId);
  const latest = useRef(reload);
  latest.current = reload;
  const seen = useRef<string | null>(null);
  const lastRun = useRef(0);
  const pending = useRef<number | undefined>(undefined);
  const signature = jobs ? jobs.map((j) => `${j.id}:${j.status}`).sort().join("|") : null;

  useEffect(() => {
    if (signature === null) {
      seen.current = null;
      return;
    }
    if (seen.current === signature) return;
    seen.current = signature;
    const wait = lastRun.current + RELOAD_MS - Date.now();
    if (wait <= 0) {
      lastRun.current = Date.now();
      latest.current();
    } else if (pending.current === undefined) {
      pending.current = window.setTimeout(() => {
        // clock, not data: it only spaces out reloads
        pending.current = undefined;
        lastRun.current = Date.now();
        latest.current();
      }, wait);
    }
  }, [signature]);

  useEffect(
    () => () => {
      window.clearTimeout(pending.current);
      pending.current = undefined;
    },
    [],
  );
}
