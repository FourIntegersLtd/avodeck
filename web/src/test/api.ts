import { http, HttpResponse, type JsonBodyType } from "msw";
import { fixturePack, launchPack, me, messages, sessionStatus, today, type Messages, type Schemas } from "./fixtures";
import { server } from "./server";

export { http, HttpResponse, server };

/** The API's error envelope (architecture 5.1). */
export function apiError(status: number, code: string, extra: Record<string, unknown> = {}) {
  return HttpResponse.json({ error: { code, message: "API text the web app never shows", ...extra } }, { status });
}

export const json = (body: JsonBodyType, status = 200) => HttpResponse.json(body, { status });

/** A signed-in person with MFA done: session, me, today and case packs. */
export function signedIn(
  opts: { me?: Schemas["Me"]; today?: Schemas["Today"]; pack?: Schemas["CasePack"]; messages?: Messages } = {},
) {
  server.use(
    http.get("/v1/notifications", () => json(opts.messages ?? messages())),
    http.get("/v1/me/notification-preferences", () => json([])),
    http.get("/v1/auth/session", () => json(sessionStatus())),
    http.get("/v1/me", () => json(opts.me ?? me())),
    http.get("/v1/today", () => json(opts.today ?? today())),
    http.get("/v1/cases/:caseId/pack", () => json(opts.pack ?? launchPack)),
    http.get("/v1/cases/:caseId/steps/progress", () => json({ steps: [] })),
    http.get("/v1/cases/:caseId/progress", () => json({ read: 0, total: 0, seconds_left: 0 })),
    http.get("/v1/me/stats/balance", () => json({ counts: {}, months: 12 })),
    http.get("/v1/auth/mfa/recovery-codes", () => json({ remaining: 10, total: 10 })),
    http.get("/v1/cell", () => json({ storage_place: null, support_phone: null, support_hours: null })),
  );
}

export function signedOut() {
  server.use(
    http.get("/v1/auth/session", () => apiError(401, "not_signed_in")),
    http.get("/v1/me", () => apiError(401, "not_signed_in")),
    http.get("/v1/cell", () => json({ storage_place: null, support_phone: null, support_hours: null })),
  );
}

export { fixturePack, launchPack };
