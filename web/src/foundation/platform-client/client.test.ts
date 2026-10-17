// The generated client wrapper: cookie session, CSRF header, Idempotency-Key, If-Match and the
// API's error envelope (web/AGENTS.md, architecture 5.1).
import { afterEach, expect, test, vi } from "vitest";
import { apiError, http, HttpResponse, json, server } from "../../test/api";
import { api, ApiError, idempotencyKey, ifMatch, onSignedOut, unwrap, websocketUrl } from ".";

afterEach(() => vi.restoreAllMocks());

test("a state-changing request sends the CSRF cookie back in X-CSRF-Token", async () => {
  document.cookie = "av_csrf=csrf-from-cookie";
  let seen: string | null = null;
  server.use(
    http.post("/v1/auth/sign-out", ({ request }) => {
      seen = request.headers.get("x-csrf-token");
      return new HttpResponse(null, { status: 204 });
    }),
  );
  await unwrap(api.POST("/v1/auth/sign-out"));
  expect(seen).toBe("csrf-from-cookie");
});

test("a read sends no CSRF header and returns the body", async () => {
  document.cookie = "av_csrf=csrf-from-cookie";
  let seen: string | null = "unset";
  server.use(
    http.get("/v1/auth/session", ({ request }) => {
      seen = request.headers.get("x-csrf-token");
      return json({ aal: "aal2", mfa_enrolled: true, mfa_complete: true, step_up_until: null, minutes_until_timeout: 30 });
    }),
  );
  const status = await unwrap(api.GET("/v1/auth/session"));
  expect(status.mfa_complete).toBe(true);
  expect(seen).toBeNull();
});

test("an error response becomes an ApiError with the API's code and extra fields", async () => {
  server.use(http.post("/v1/me/sessions/revoke-all", () => apiError(403, "step_up_required")));
  const err = await unwrap(api.POST("/v1/me/sessions/revoke-all")).catch((e: unknown) => e);
  expect(err).toBeInstanceOf(ApiError);
  expect(err).toMatchObject({ status: 403, code: "step_up_required" });

  server.use(http.get("/v1/jobs/:id", () => apiError(503, "processing_paused", { reason: "cost_guard" })));
  const paused = await unwrap(api.GET("/v1/jobs/{job_id}", { params: { path: { job_id: "j" } } })).catch((e: unknown) => e);
  expect(paused).toMatchObject({ status: 503, code: "processing_paused", extra: { reason: "cost_guard" } });
});

test("a response that is not the error envelope still becomes an ApiError", async () => {
  server.use(http.get("/v1/me", () => new HttpResponse("bad gateway", { status: 502 })));
  await expect(unwrap(api.GET("/v1/me"))).rejects.toMatchObject({ status: 502, code: "server_error" });
});

test("a 401 tells listeners the person is signed out", async () => {
  const listener = vi.fn();
  const stop = onSignedOut(listener);
  server.use(http.get("/v1/me", () => apiError(401, "session_expired")));
  await unwrap(api.GET("/v1/me")).catch(() => undefined);
  stop();
  expect(listener).toHaveBeenCalledWith("session_expired");
});

test("idempotencyKey gives a fresh key for each action, and ifMatch carries the ETag", () => {
  const a = idempotencyKey()["Idempotency-Key"];
  const b = idempotencyKey()["Idempotency-Key"];
  expect(a).toMatch(/^[0-9a-f-]{36}$/);
  expect(a).not.toBe(b);
  expect(ifMatch('"v3"')).toEqual({ "If-Match": '"v3"' });
});

test("websocketUrl points at the API origin over ws(s)", () => {
  expect(websocketUrl("/v1/cases/c1/dictation?context=opinion")).toBe(`ws://${window.location.host}/v1/cases/c1/dictation?context=opinion`);
});
