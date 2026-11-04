// The live hub: the only place in the app that opens an EventSource. One stream per open case
// (GET /v1/cases/{id}/events) carries the progress of every background job on that case. Every screen
// that waits listens to it through the hooks below, so the browser never uses more than one connection
// per case and never polls. A stream is opened by the first listener and closed by the last.
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
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
  retry?: number;
  count: number;
  jobs: LiveJob[] | null;
  listeners: Set<() => void>;
}

const CLOSED = 2; // EventSource.readyState: the browser has given up on this stream
const RELOAD_MS = 2000;
const RECONNECT_MS = [2000, 5000, 15000, 30000]; // the browser gives up on a refused stream for good, so we reopen it ourselves
const entries = new Map<string, Entry>();

/** Keep the old object for every job that has not changed, so a screen only redraws for its own job. */
function share(before: LiveJob[] | null, next: LiveJob[]): LiveJob[] {
  const old = new Map((before ?? []).map((j) => [j.id, j]));
  return next.map((j) => {
    const was = old.get(j.id);
    return was && JSON.stringify(was) === JSON.stringify(j) ? was : j;
  });
}

function connect(caseId: string, entry: Entry, attempt: number): void {
  const source = new EventSource(`${apiOrigin()}/v1/cases/${caseId}/events`, { withCredentials: true });
  entry.source = source;
  source.onmessage = (e: MessageEvent<string>) => {
    attempt = 0;
    const data = JSON.parse(e.data) as { jobs: LiveJob[] };
    entry.jobs = share(entry.jobs, data.jobs);
    entry.listeners.forEach((listener) => listener());
  };
  source.onerror = () => {
    // While the browser is retrying by itself there is nothing to do. A stream it has given up on was
    // refused (signed out, no access, or a gateway hiccup): ask the normal way, which sends a signed-out
    // person to sign in, then open a fresh stream after a pause that grows.
    if (source.readyState !== CLOSED) return;
    void unwrap(api.GET("/v1/auth/session")).catch(() => undefined);
    const wait = RECONNECT_MS[Math.min(attempt, RECONNECT_MS.length - 1)]!;
    entry.retry = window.setTimeout(() => { // clock, not data: it spaces out reconnects
      entry.retry = undefined;
      if (entries.get(caseId) === entry && entry.count > 0) connect(caseId, entry, attempt + 1);
    }, wait);
  };
}

function open(caseId: string): Entry {
  const entry = { count: 0, jobs: null, listeners: new Set() } as unknown as Entry;
  connect(caseId, entry, 0);
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
      window.clearTimeout(mine.retry);
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
      pending.current = window.setTimeout(() => { // clock, not data: it only spaces out reloads
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

const ENDED_AS = (status: string): "done" | "failed" | null => (status === "done" ? "done" : status === "failed" || status === "cancelled" ? "failed" : null);

/** How a job a screen is waiting on ended: "done", "failed", or "slow" (still going when `timeoutMs` ran out).
 * Null while it is running. The case stream answers almost always; the server is asked once, never in a
 * loop, when the job is missing from the stream's first list (it ended too long ago to be listed) and once
 * when the time is up, so a wait can never go on for ever. */
export function useJobOutcome(caseId: string | null, jobId: string | null, timeoutMs: number): "done" | "failed" | "slow" | null {
  const jobs = useCaseJobs(caseId);
  const live = jobId ? jobs?.find((j) => j.id === jobId) : undefined;
  const [asked, setAsked] = useState<{ id: string; outcome: "done" | "failed" | "slow" } | null>(null);
  const checked = useRef<string | null>(null);

  const ask = useCallback(
    async (final: boolean) => {
      if (!jobId) return;
      try {
        const got = await unwrap(api.GET("/v1/jobs/{job_id}", { params: { path: { job_id: jobId } } }));
        const outcome = ENDED_AS(got.status) ?? (final ? "slow" : null);
        if (outcome) setAsked({ id: jobId, outcome });
      } catch {
        if (final) setAsked({ id: jobId, outcome: "slow" });
      }
    },
    [jobId],
  );

  useEffect(() => {
    if (!jobId) {
      checked.current = null;
      return;
    }
    if (jobs !== null && !live && checked.current !== jobId) {
      checked.current = jobId; // the stream has spoken and does not list this job
      void ask(false);
    }
  }, [jobId, jobs, live, ask]);

  useEffect(() => {
    if (!jobId) return;
    const timer = setTimeout(() => void ask(true), timeoutMs); // clock, not data: one last question, never repeated
    return () => clearTimeout(timer);
  }, [jobId, timeoutMs, ask]);

  const fromStream = live ? ENDED_AS(live.status) : null;
  return fromStream ?? (asked && asked.id === jobId ? asked.outcome : null);
}

const ENDED = new Set(["done", "failed", "cancelled"]);
const signatureOf = (jobs: LiveJob[] | null) => (jobs ? jobs.map((j) => `${j.id}:${j.status}`).sort().join("|") : null);
const stopped = (name: "AbortError" | "TimeoutError", message: string) => Object.assign(new Error(message), { name });

/** For code that is not a screen (a download, an upload): listen to a case until `settle` says it is
 * done. Stops, and lets the stream go, on `signal` or after `timeoutMs`. */
function listen<T>(caseId: string, opts: { signal?: AbortSignal; timeoutMs: number }, settle: (jobs: LiveJob[] | null) => T | undefined): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    if (opts.signal?.aborted) return reject(stopped("AbortError", "stopped"));
    let stop = () => undefined as void;
    const end = (finish: () => void) => {
      clearTimeout(timer);
      opts.signal?.removeEventListener("abort", onAbort);
      stop();
      finish();
    };
    const onAbort = () => end(() => reject(stopped("AbortError", "stopped")));
    const timer = setTimeout(() => end(() => reject(stopped("TimeoutError", "took too long"))), opts.timeoutMs); // clock, not data
    opts.signal?.addEventListener("abort", onAbort, { once: true });
    const check = () => {
      const found = settle(caseJobsNow(caseId));
      if (found !== undefined) end(() => resolve(found));
    };
    stop = subscribeToCase(caseId, check);
    check(); // a snapshot may already be held
  });
}

/** Settles with a job when it has ended (done, failed or cancelled). */
export function waitForJob(caseId: string, jobId: string, opts: { signal?: AbortSignal; timeoutMs: number }): Promise<LiveJob> {
  return listen(caseId, opts, (jobs) => {
    const found = jobs?.find((j) => j.id === jobId);
    return found && ENDED.has(found.status) ? found : undefined;
  });
}

/** Follows a case for a code path that looks at the server again each time something may have changed
 * (an upload finishing). One stream is held from here until `close`, so looking again never reopens it.
 * `changed` settles when a job's status changed or a job appeared or went since the last time it settled,
 * and the first snapshot counts if none was held to begin with. Counts moving never settle it. */
export function followCase(caseId: string): { changed: (opts: { signal?: AbortSignal; timeoutMs: number }) => Promise<void>; close: () => void } {
  let last: string | null = null;
  let dirty = false;
  let wake: (() => void) | null = null;
  const stop = subscribeToCase(caseId, () => {
    const now = signatureOf(caseJobsNow(caseId));
    if (now === null || now === last) return;
    last = now;
    dirty = true;
    wake?.();
  });
  last = signatureOf(caseJobsNow(caseId)); // what was held when following began (null: nothing yet)
  return {
    close: stop,
    changed: (opts) =>
      new Promise<void>((resolve, reject) => {
        if (opts.signal?.aborted) return reject(stopped("AbortError", "stopped"));
        if (dirty) {
          dirty = false;
          return resolve();
        }
        const end = (finish: () => void) => {
          clearTimeout(timer);
          opts.signal?.removeEventListener("abort", onAbort);
          wake = null;
          finish();
        };
        const onAbort = () => end(() => reject(stopped("AbortError", "stopped")));
        const timer = setTimeout(() => end(() => reject(stopped("TimeoutError", "took too long"))), opts.timeoutMs); // clock, not data
        opts.signal?.addEventListener("abort", onAbort, { once: true });
        wake = () =>
          end(() => {
            dirty = false;
            resolve();
          });
      }),
  };
}

/** Settles once when something on the case may have changed: see `followCase`. */
export async function waitForCaseChange(caseId: string, opts: { signal?: AbortSignal; timeoutMs: number }): Promise<void> {
  const follow = followCase(caseId);
  try {
    await follow.changed(opts);
  } finally {
    follow.close();
  }
}
