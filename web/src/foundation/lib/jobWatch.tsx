// Follows processing jobs for the whole signed-in app, so the person can leave the screen that
// started one and still be told when it ends. Lives above the routes, so a change of screen keeps it.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";
import { api, apiOrigin, unwrap } from "../platform-client";
import { useToast } from "./toasts";

export interface JobWords {
  done: string;
  failed: string;
  /** The link's label and where it goes. */
  open?: string;
  to?: string;
}
type Update = { status?: string };

const POLL_MS = 3000;
const JobWatchContext = createContext<{ watch: (jobId: string, words: JobWords) => void } | null>(null);

export function JobWatchProvider({ children }: { children: ReactNode }) {
  const toast = useToast();
  const open = useRef(new Map<string, { close: () => void }>());

  const watch = useCallback(
    (jobId: string, words: JobWords) => {
      if (open.current.has(jobId)) return;
      const action = words.open && words.to ? { label: words.open, to: words.to } : undefined;
      const end = () => {
        open.current.get(jobId)?.close();
        open.current.delete(jobId);
      };
      const onUpdate = (update: Update) => {
        if (update.status === "done") toast({ text: words.done, action });
        else if (update.status === "failed") toast({ text: words.failed, tone: "error" });
        else if (update.status !== "cancelled") return;
        end();
      };
      if (typeof EventSource !== "undefined") {
        const events = new EventSource(`${apiOrigin()}/v1/jobs/${jobId}/events`, { withCredentials: true });
        events.onmessage = (e: MessageEvent<string>) => onUpdate(JSON.parse(e.data) as Update);
        open.current.set(jobId, events);
      } else {
        const timer = window.setInterval(() => {
          unwrap(api.GET("/v1/jobs/{job_id}", { params: { path: { job_id: jobId } } })).then(onUpdate, () => undefined);
        }, POLL_MS);
        open.current.set(jobId, { close: () => window.clearInterval(timer) });
      }
    },
    [toast],
  );

  useEffect(() => {
    const jobs = open.current;
    return () => jobs.forEach((j) => j.close());
  }, []);

  const value = useMemo(() => ({ watch }), [watch]);
  return <JobWatchContext.Provider value={value}>{children}</JobWatchContext.Provider>;
}

/** Ask to be told when a job ends. Outside the provider it does nothing. */
export function useJobWatch(): { watch: (jobId: string, words: JobWords) => void } {
  return useContext(JobWatchContext) ?? { watch: () => undefined };
}
