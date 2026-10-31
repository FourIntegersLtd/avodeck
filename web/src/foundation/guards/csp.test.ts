// Rule 4: the browser talks only to FastAPI. With the API on its own origin (ADR 0031) each directive
// may name the page's own origin, the one configured API origin, and the object-URL schemes the page
// makes from bytes FastAPI streamed — nothing else, and never a wildcard.
import { expect, test } from "vitest";
import { contentSecurityPolicy } from "../../../csp";

const API = "https://uk.api.example.test";

function directive(policy: string, name: string): string[] {
  const found = policy
    .split(";")
    .map((d) => d.trim().split(/\s+/))
    .find((parts) => parts[0] === name);
  return found ? found.slice(1) : [];
}

test("same-origin build: pictures come from the page's origin, data: and blob: only", () => {
  expect(new Set(directive(contentSecurityPolicy(), "img-src"))).toEqual(new Set(["'self'", "data:", "blob:"]));
});

test("cross-origin build: the signature picture may come from the API, and nowhere else", () => {
  // SignSend.tsx shows /v1/me/signature/image straight from the API, so img-src must name that origin.
  expect(new Set(directive(contentSecurityPolicy(API), "img-src"))).toEqual(
    new Set(["'self'", "data:", "blob:", API]),
  );
});

test("the API origin is the only host any directive may name", () => {
  const policy = contentSecurityPolicy(API);
  const allowed = new Set(["'self'", "'none'", "data:", "blob:", API, API.replace(/^http/, "ws")]);
  for (const part of policy.split(";")) {
    const [name, ...sources] = part.trim().split(/\s+/);
    for (const source of sources) expect(allowed.has(source!), `${name} ${source}`).toBe(true);
  }
});

test("no directive is ever a wildcard", () => {
  for (const policy of [contentSecurityPolicy(), contentSecurityPolicy(API)]) {
    expect(policy).not.toContain("*");
    expect(policy).not.toContain("unsafe");
  }
});
