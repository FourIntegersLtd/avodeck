import { describe, expect, it } from "vitest";
import { buildPolicy } from "./csp";

describe("buildPolicy", () => {
  it("allows scripts only from this origin or carrying this request's nonce", () => {
    const policy = buildPolicy("abc123", { apiOrigin: "", isDev: false });
    expect(policy).toContain("script-src 'self' 'nonce-abc123' 'strict-dynamic'");
    expect(policy).not.toContain("unsafe-inline");
    expect(policy).not.toContain("unsafe-eval");
  });

  it("keeps styles to this origin or the nonce, with no inline allowance", () => {
    expect(buildPolicy("n", { apiOrigin: "", isDev: false })).toContain("style-src 'self' 'nonce-n'");
  });

  it("lets the browser talk only to this origin, plus the API origin when it is separate", () => {
    expect(buildPolicy("n", { apiOrigin: "", isDev: false })).toContain("connect-src 'self';");
    expect(buildPolicy("n", { apiOrigin: "https://api.example.test/", isDev: false })).toContain(
      "connect-src 'self' https://api.example.test wss://api.example.test;",
    );
  });

  it("allows eval only in development, where Next's reload needs it", () => {
    expect(buildPolicy("n", { apiOrigin: "", isDev: true })).toContain("'unsafe-eval'");
  });

  it("never lets the page be framed, and allows no plugins", () => {
    const policy = buildPolicy("n", { apiOrigin: "", isDev: false });
    expect(policy).toContain("frame-ancestors 'none'");
    expect(policy).toContain("object-src 'none'");
  });
});
