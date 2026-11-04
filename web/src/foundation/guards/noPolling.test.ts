// No polling in the browser. Everything that changes while a person watches (a job's progress, a build
// finishing, a reading ending) arrives over the one live stream per case in foundation/lib/sse.ts. This
// guard keeps it that way: no second EventSource, no timer that asks the server again, no POLL_* wait.
import { readFileSync } from "node:fs";
import { relative } from "node:path";
import { describe, expect, test } from "vitest";
import { files, isTest, WEB } from "./walk";

const HUB = "src/foundation/lib/sse.ts";
const CLOCK = "clock, not data"; // marks a timer that only counts time or spaces calls out, and reads nothing

const sources = files(`${WEB}/src`, (rel) => /\.tsx?$/.test(rel) && !isTest(rel) && !rel.includes("/platform-client/")).map((full) => {
  const rel = relative(WEB, full).split("\\").join("/");
  return { rel, lines: readFileSync(full, "utf8").split("\n") };
});

const hits = (test: (line: string) => boolean, skip: (rel: string) => boolean = () => false) =>
  sources.flatMap(({ rel, lines }) =>
    skip(rel) ? [] : lines.flatMap((line, i) => (test(line) && !line.includes(CLOCK) ? [`${rel}:${i + 1}: ${line.trim()}`] : [])),
  );

describe("no polling in the browser", () => {
  test("only the live hub opens an EventSource", () => {
    expect(hits((l) => /new\s+EventSource\b/.test(l), (rel) => rel === HUB)).toEqual([]);
  });

  test("no interval timer unless it is marked as a clock", () => {
    expect(hits((l) => /\bsetInterval\s*\(/.test(l))).toEqual([]);
  });

  test("no POLL_* wait or polling interval", () => {
    expect(hits((l) => /\bPOLL_[A-Z_]+\b|refetchInterval|pollInterval/.test(l))).toEqual([]);
  });

  test("no timer that loads or reloads data when it fires", () => {
    expect(hits((l) => /setTimeout\(\s*(void\s+)?(reload|refresh|refetch|load)\b/.test(l))).toEqual([]);
  });

  test("the hub is still the one place a case's live jobs come from", () => {
    const hub = sources.find((s) => s.rel === HUB)!.lines.join("\n");
    expect(hub).toMatch(/new EventSource\(/);
    expect(hub).toMatch(/\/v1\/cases\/\$\{caseId\}\/events/);
  });
});
