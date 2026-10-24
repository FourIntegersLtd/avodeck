// Answers the screens have loaded, kept in memory so a screen seen before shows at once while it
// asks the server again (useResource). Memory only: nothing here outlives the tab.
//
// What keeps it from showing the wrong thing:
// - only successful answers are kept (useResource never stores an error, and drops the entry on one);
// - any change sent to the API (POST, PUT, PATCH, DELETE), an ended session and a different person
//   signed in empty it;
// - an answer that set off before a change is not kept (the generation moved on while it travelled).
import { onMutation, onSignedOut } from "../platform-client";

const MAX_ENTRIES = 200;
const entries = new Map<string, { value: unknown }>();
let generation = 0;
let owner: string | undefined;

/** Empties the cache; anything still on its way will not be kept. */
export function clearResourceCache(): void {
  entries.clear();
  generation += 1;
}

/** The person whose answers the cache holds; a different person empties it. */
export function claimResourceCache(personId: string): void {
  if (owner !== undefined && owner !== personId) clearResourceCache();
  owner = personId;
}

/** The entry name for a call site's cache name and its arguments, or undefined if they are not plain values. */
export function cacheKey(name: string | undefined, args: readonly unknown[]): string | undefined {
  if (!name) return undefined;
  const plain = args.every((a) => a === null || a === undefined || ["string", "number", "boolean"].includes(typeof a));
  return plain ? `${name}\u0000${JSON.stringify(args.map((a) => (a === undefined ? null : a)))}` : undefined;
}

export function readCache<T>(key: string): { value: T } | undefined {
  const hit = entries.get(key);
  if (!hit) return undefined;
  entries.delete(key); // most recently used goes last
  entries.set(key, hit);
  return hit as { value: T };
}

/** The current generation: pass it back to `writeCache` so an answer from before a change is dropped. */
export function cacheGeneration(): number {
  return generation;
}

export function writeCache(key: string, value: unknown, startedAt: number): void {
  if (startedAt !== generation) return;
  entries.delete(key);
  entries.set(key, { value });
  while (entries.size > MAX_ENTRIES) entries.delete(entries.keys().next().value as string);
}

export function dropCache(key: string): void {
  entries.delete(key);
}

onMutation(clearResourceCache);
onSignedOut(clearResourceCache);
