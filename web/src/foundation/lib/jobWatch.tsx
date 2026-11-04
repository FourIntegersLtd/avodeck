// Follows processing jobs for the whole signed-in app, so the person can leave the screen that
// started one and still be told when it ends. It listens through the live hub (sse.ts), so watching
// several jobs of one case shares one connection. Lives above the routes, so a change of screen keeps it.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";
import { caseJobsNow, subscribeToCase } from "./sse";
import { useToast } from "./toasts";

export interface JobWords {
  done: string;
  failed: string;
  /** The link's label and where it goes. */
  open?: string;
  to?: string;
}

type Watch = (caseId: string, jobId: string, words: JobWords) => void;
const JobWatchContext = createContext<{ watch: Watch } | null>(null);

export function JobWatchProvider({ children }: { children: ReactNode }) {
  const toast = useToast();
  const open = useRef(new Map<string, () => void>());

  const watch = useCallback<Watch>(
    (caseId, jobId, words) => {
      if (open.current.has(jobId)) return;
      const action = words.open && words.to ? { label: words.open, to: words.to } : undefined;
      const look = () => {
        const status = caseJobsNow(caseId)?.find((j) => j.id === jobId)?.status;
        if (status === "done") toast({ text: words.done, action });
        else if (status === "failed") toast({ text: words.failed, tone: "error" });
        else if (status !== "cancelled") return;
        open.current.get(jobId)?.();
        open.current.delete(jobId);
      };
      open.current.set(jobId, subscribeToCase(caseId, look));
    },
    [toast],
  );

  useEffect(() => {
    const jobs = open.current;
    return () => jobs.forEach((stop) => stop());
  }, []);

  const value = useMemo(() => ({ watch }), [watch]);
  return <JobWatchContext.Provider value={value}>{children}</JobWatchContext.Provider>;
}

/** Ask to be told when a job ends. Outside the provider it does nothing. */
export function useJobWatch(): { watch: Watch } {
  return useContext(JobWatchContext) ?? { watch: () => undefined };
}
