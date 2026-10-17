// @vitest-environment node
// Clickjacking: the hosted page refuses to be framed by any site. A meta CSP cannot carry
// frame-ancestors, so the host sends it as a header (web/vercel.json) on every path, with
// X-Frame-Options for older browsers, nosniff and no referrer.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "vitest";
import { WEB } from "./walk";

type Header = { key: string; value: string };
type Rule = { source: string; headers: Header[] };

function rules(): Rule[] {
  const path = join(WEB, "vercel.json");
  expect(existsSync(path), "web/vercel.json sets the response headers").toBe(true);
  return (JSON.parse(readFileSync(path, "utf8")).headers ?? []) as Rule[];
}

test("every path is sent with headers that forbid framing", () => {
  const all = rules().find((r) => r.source === "/(.*)");
  expect(all, "a header rule for every path").toBeDefined();
  const sent = Object.fromEntries(all!.headers.map((h) => [h.key.toLowerCase(), h.value]));
  expect(sent["x-frame-options"]).toBe("DENY");
  expect(sent["content-security-policy"]).toMatch(/frame-ancestors 'none'/);
  expect(sent["x-content-type-options"]).toBe("nosniff");
  expect(sent["referrer-policy"]).toBe("no-referrer");
});
