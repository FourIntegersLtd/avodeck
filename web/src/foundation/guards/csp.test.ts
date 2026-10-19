// The policy lets a page picture be shown from an object URL (blob:) the browser made from the bytes FastAPI
// streamed, and still from nowhere but the page's own origin and data: (rule 4: no other image origin).
import { expect, test } from "vitest";
import { contentSecurityPolicy } from "../../../csp";

function directive(policy: string, name: string): string[] {
  const found = policy
    .split(";")
    .map((d) => d.trim().split(/\s+/))
    .find((parts) => parts[0] === name);
  return found ? found.slice(1) : [];
}

test("pictures may come from the page's origin, data: and blob: only", () => {
  expect(new Set(directive(contentSecurityPolicy(), "img-src"))).toEqual(new Set(["'self'", "data:", "blob:"]));
  expect(new Set(directive(contentSecurityPolicy("https://uk.api.example.test"), "img-src"))).toEqual(new Set(["'self'", "data:", "blob:"]));
});
