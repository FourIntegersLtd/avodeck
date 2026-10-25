// @vitest-environment node
// Rule 4: the browser renders only. Search, filters, sort order and counts come from the API; a
// screen never filters a fetched list by typed text. The offline witness-box search (S25) is the
// one allowed exception, because it must work with no connection.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "vitest";
import { files, isTest, WEB } from "./walk";

const MATCHING = /\.(toLowerCase|toLocaleLowerCase)\(\s*[^)]*\)\s*\.includes\(/;
const ALLOWED = new Set(["src/capabilities/trial/Lookup.tsx", "src/capabilities/trial/search.ts"]);

const matchesInBrowser = (text: string) => MATCHING.test(text);

test("no capability filters a fetched list by typed text in the browser", () => {
  const source = files(join(WEB, "src/capabilities"), (rel) => /\.tsx?$/.test(rel) && !isTest(rel) && !ALLOWED.has(rel));
  expect(source.length).toBeGreaterThan(0);
  const offenders = source.filter((p) => matchesInBrowser(readFileSync(p, "utf8"))).map((p) => p.slice(WEB.length + 1));
  expect(offenders, "ask the API to search (a `q` parameter) instead").toEqual([]);
});

test("the pattern catches client-side matching", () => {
  expect(matchesInBrowser("rows.filter((r) => r.label.toLowerCase().includes(query))")).toBe(true);
  expect(matchesInBrowser("a.toLocaleLowerCase().includes(b)")).toBe(true);
  expect(matchesInBrowser("name.trim().length")).toBe(false);
});
