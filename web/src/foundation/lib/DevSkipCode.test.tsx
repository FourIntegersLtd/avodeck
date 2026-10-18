// Local development only: the code screens pass themselves. The dev server (web/devMfa.ts) keeps
// each account's authenticator key from setup and answers its current code; the API still checks it.
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { http, json, server } from "../../test/api";
import { DevSkipCode, rememberDevEmail } from "./DevSkipCode";

afterEach(() => vi.unstubAllEnvs());

function devServer(code = "123456") {
  const seen: { secret?: unknown; codeFor?: string | null; verified?: unknown } = {};
  server.use(
    http.post("/__dev/mfa-secret", async ({ request }) => {
      seen.secret = await request.json();
      return new Response(null, { status: 204 });
    }),
    http.get("/__dev/mfa-code", ({ request }) => {
      seen.codeFor = new URL(request.url).searchParams.get("email");
      return new Response(code);
    }),
    http.post("/v1/auth/mfa/verify", async ({ request }) => {
      seen.verified = await request.json();
      return json({ aal: "aal2", recovery_codes: [] });
    }),
  );
  return seen;
}

test("under the dev server the code screen passes itself for the account that signed in", async () => {
  vi.stubEnv("MODE", "development");
  const seen = devServer();
  const onVerified = vi.fn();
  rememberDevEmail("demo@example.test");
  render(<DevSkipCode onVerified={onVerified} />);
  await waitFor(() => expect(onVerified).toHaveBeenCalled());
  expect(seen.codeFor).toBe("demo@example.test");
  expect(seen.verified).toEqual({ code: "123456" });
});

test("at setup the new authenticator key goes to the dev server before the code is asked for", async () => {
  vi.stubEnv("MODE", "development");
  const seen = devServer();
  const onVerified = vi.fn();
  rememberDevEmail("demo@example.test");
  render(<DevSkipCode onVerified={onVerified} secret="JBSWY3DPEHPK3PXP" />);
  await waitFor(() => expect(onVerified).toHaveBeenCalled());
  expect(seen.secret).toEqual({ email: "demo@example.test", secret: "JBSWY3DPEHPK3PXP" });
});

test("in tests it waits for the button instead of passing by itself", async () => {
  const seen = devServer();
  const onVerified = vi.fn();
  render(<DevSkipCode onVerified={onVerified} />);
  expect(screen.getByRole("button", { name: "Skip (local only)" })).toBeInTheDocument();
  await new Promise((r) => setTimeout(r, 20));
  expect(onVerified).not.toHaveBeenCalled();
  expect(seen.verified).toBeUndefined();
});
