// Shared by every scenario: the staging guard, sign-in, request helper and the numbers we judge runs by.
//
// Nothing here prints a response body, an address or a credential (rule 6). Requests are tagged with a short
// route name, never the real path, so result files carry no case or page identifiers.
import http from "k6/http";
import { check } from "k6";
import { SharedArray } from "k6/data";
import { Counter, Rate, Trend } from "k6/metrics";
import { requireStaging } from "./target.js";

// Refuses to run (at k6 start-up, before any request) unless STAGING_URL passes the allow-list.
export const BASE = requireStaging(__ENV);

export const serverErrors = new Rate("server_errors"); // any 5xx
export const busy503 = new Rate("busy_503"); // 503 with the `busy` code
export const throttled429 = new Rate("throttled_429");
export const signInFailures = new Counter("sign_in_failures");
export const loopLagP99Ms = new Trend("loop_lag_ms_p99");

// A JSON file outside the repository: [{"email": ..., "password": ..., "case_id": ...}, ...] for synthetic staging
// accounts only. Its path comes from LOAD_USERS_FILE; the repository never holds it.
const users = new SharedArray("users", () => {
  if (!__ENV.LOAD_USERS_FILE) throw new Error("LOAD_USERS_FILE is not set (see scripts/load/README.md).");
  return JSON.parse(open(__ENV.LOAD_USERS_FILE));
});

export function userCount() {
  return users.length;
}

// The i-th synthetic account (1-based, wraps). Callers never log the object.
export function userAt(i) {
  return users[(i - 1) % users.length];
}

export function intFromEnv(name, fallback) {
  const raw = __ENV[name];
  if (raw === undefined || raw === "") return fallback;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 0) throw new Error(`${name} must be a whole number.`);
  return n;
}

function headersFor(method, extra) {
  const headers = Object.assign({ accept: "application/json" }, extra || {});
  if (__ENV.LOAD_WEB_ORIGIN) headers.origin = __ENV.LOAD_WEB_ORIGIN;
  if (method !== "GET" && method !== "HEAD") {
    const csrf = http.cookieJar().cookiesForURL(`${BASE}/`)["av_csrf"];
    if (csrf && csrf.length > 0) headers["x-csrf-token"] = csrf[0];
  }
  return headers;
}

function record(res) {
  serverErrors.add(res.status >= 500);
  throttled429.add(res.status === 429);
  busy503.add(res.status === 503 && typeof res.body === "string" && res.body.indexOf('"busy"') !== -1);
}

// One request. `route` is a short fixed name (e.g. "reader_page") used as the metric tag; `kind` groups routes for
// thresholds ("reader_page" for the reader's three calls, "api" for everything else).
export function call(method, path, { route, kind = "api", json, body, headers, timeout, responseType } = {}) {
  const sent = Object.assign({}, headers || {});
  let payload = body === undefined ? null : body;
  if (json !== undefined) {
    payload = JSON.stringify(json);
    sent["content-type"] = "application/json";
  }
  const params = {
    headers: headersFor(method, sent),
    tags: { name: route, kind },
  };
  if (timeout) params.timeout = timeout;
  if (responseType) params.responseType = responseType;
  const res = http.request(method, `${BASE}${path}`, payload, params);
  record(res);
  return res;
}

export function signIn(user) {
  const res = call("POST", "/v1/auth/sign-in", {
    route: "sign_in",
    json: { email: user.email, password: user.password },
  });
  const ok = check(res, { "signed in": (r) => r.status === 200 });
  if (!ok) signInFailures.add(1);
  return ok;
}

// Reads the API's own numbers (pool, loop lag, queues). Needs OPS_METRICS_TOKEN in the environment; without it
// nothing is read and the loop-lag threshold sees no samples.
export function scrapeMetrics() {
  if (!__ENV.OPS_METRICS_TOKEN) return null;
  const res = http.get(`${BASE}/v1/internal/metrics`, {
    headers: { authorization: `Bearer ${__ENV.OPS_METRICS_TOKEN}` },
    tags: { name: "ops_metrics", kind: "ops" },
  });
  if (res.status !== 200) return null;
  const snapshot = res.json();
  if (typeof snapshot.loop_lag_ms_p99 === "number") loopLagP99Ms.add(snapshot.loop_lag_ms_p99);
  return snapshot;
}

// Number of seconds in a k6 duration string such as "90m".
export function seconds(text) {
  const m = /^(\d+)(s|m|h)$/.exec(text);
  if (m === null) throw new Error("A duration looks like 30s, 90m or 2h.");
  return Number(m[1]) * { s: 1, m: 60, h: 3600 }[m[2]];
}
