// The screen cache (foundation/lib/resourceCache.ts) is safe only if every cache name belongs to one
// call site, and only if it never leaves memory: patient data must not persist on the device.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "vitest";
import { files, isTest, WEB } from "./walk";

const SRC = join(WEB, "src");

test("every useResource cache name is used by exactly one call site", () => {
  const seen = new Map<string, string[]>();
  for (const file of files(SRC, (rel) => /\.tsx?$/.test(rel) && !isTest(rel))) {
    const text = readFileSync(file, "utf8");
    for (const match of text.matchAll(/\{\s*cache:\s*"([^"]+)"\s*\}/g)) {
      seen.set(match[1]!, [...(seen.get(match[1]!) ?? []), file.slice(SRC.length + 1)]);
    }
  }
  const shared = [...seen].filter(([, files]) => files.length > 1);
  expect(shared).toEqual([]);
  expect(seen.size).toBeGreaterThan(50); // the screens use it
});

test("the cache keeps nothing in browser storage", () => {
  const text = readFileSync(join(SRC, "foundation", "lib", "resourceCache.ts"), "utf8");
  expect(text).not.toMatch(/localStorage|sessionStorage|indexedDB|caches\.|document\.cookie/);
});
