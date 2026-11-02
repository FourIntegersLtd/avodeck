// Answers the screens have loaded, kept in memory (TanStack Query) so a screen seen before shows at once
// and is not asked for again for 30 seconds. Memory only: nothing here outlives the tab.
//
// What keeps it from showing the wrong thing:
// - only successful answers are shown (useResource hides data once a load is refused; a network or server
//   fault on a background reload keeps what is already shown);
// - a change to one case (POST, PUT, PATCH, DELETE on /v1/cases/{id}/...) drops that case's answers and
//   Today; any other change, an ended session and a different person signed in empty everything.
import { notifyManager, QueryClient } from "@tanstack/react-query";
import { ApiError, onMutation, onSignedOut } from "../platform-client";

// Tell screens the moment an answer arrives (a microtask), not a timer tick later, as useResource did before.
notifyManager.setScheduler(queueMicrotask);

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 600_000,
      refetchOnWindowFocus: false, // a reload the person did not ask for must never replace what they are typing in
      // One more try for a network or server fault; an answer like 423 or 404 is final.
      retry: (count, error) => count < 1 && !(error instanceof ApiError && error.status >= 400 && error.status < 500),
    },
  },
});

let owner: string | undefined;
let generation = 0;

/** The current generation: an answer that set off before the cache was emptied is not kept (see useResource). */
export function cacheGeneration(): number {
  return generation;
}

/** Empties the cache; anything still on its way will not be kept. */
export function clearResourceCache(): void {
  generation += 1;
  void queryClient.cancelQueries();
  queryClient.clear();
}

/** The person whose answers the cache holds; a different person empties it. */
export function claimResourceCache(personId: string): void {
  if (owner !== undefined && owner !== personId) clearResourceCache();
  owner = personId;
}

/** The entry name for a call site's cache name and its arguments, or undefined if they are not plain values. */
export function cacheKey(name: string | undefined, args: readonly unknown[]): unknown[] | undefined {
  if (!name) return undefined;
  const plain = args.every((a) => a === null || a === undefined || ["string", "number", "boolean"].includes(typeof a));
  return plain ? [name, ...args.map((a) => (a === undefined ? null : a))] : undefined;
}

const CASE_PATH = /^\/v1\/cases\/([^/]+)(\/|$)/;

/** Drops what a change may have made out of date: one case's answers and Today, or all if not case-scoped. */
function onChange({ path }: { path: string }): void {
  const caseId = CASE_PATH.exec(path)?.[1];
  if (!caseId || path === "/v1/cases") return clearResourceCache();
  const affected = (q: { queryKey: readonly unknown[] }): boolean => {
    const [name, ...args] = q.queryKey;
    // Today, the credit balance and messages carry no case id but change with a case.
    return args.includes(caseId) || (typeof name === "string" && /today|balance|messages/i.test(name));
  };
  const invalidate = () => void queryClient.invalidateQueries({ refetchType: "none", predicate: affected }); // screens on show reload themselves; the rest refetch when next shown
  invalidate();
  // A read already on its way may show the state from before this change: drop it, then mark stale again.
  void queryClient.cancelQueries({ predicate: affected }).then(invalidate);
}

onMutation(onChange);
onSignedOut(clearResourceCache);
