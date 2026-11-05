// Scenario 3 (and the soak): 300 experts online.
//
//   k6 run scripts/load/experts.js
//   k6 run -e LOAD_HOLD=2h scripts/load/experts.js        # the soak: deploy the worker once while it runs
//
// Users ramp up over 10 minutes and then hold. Each one:
//   - keeps one case event stream open (the server ends a stream after a while; the script opens a new one),
//   - reads a page every 20 seconds (page, image, mark as read, facts),
//   - searches the bundle every 5 minutes and opens the timeline every 10 minutes.
// Pass: API p95 at most 400 ms, reader page p95 at most 300 ms, 5xx under 0.1%, 503 `busy` under 0.5%, loop lag p99
// at most 100 ms (read from the ops metrics when OPS_METRICS_TOKEN is set).
//
// Run on cases whose bundle has already been processed. Accounts are reused by index, so the file needs at least
// LOAD_EXPERTS accounts.
import { sleep } from "k6";
import http from "k6/http";
import { BASE, call, intFromEnv, scrapeMetrics, seconds, signIn, userAt, userCount } from "./lib/common.js";

const EXPERTS = intFromEnv("LOAD_EXPERTS", 300);
const RAMP = __ENV.LOAD_RAMP || "10m";
const HOLD = __ENV.LOAD_HOLD || "20m";
const PAGES = intFromEnv("LOAD_PAGES", 4000);
if (EXPERTS > userCount()) throw new Error("LOAD_USERS_FILE has fewer accounts than LOAD_EXPERTS.");

const PAGE_EVERY_MS = 20 * 1000;
const SEARCH_EVERY_MS = 5 * 60 * 1000;
const TIMELINE_EVERY_MS = 10 * 60 * 1000;
const STREAM_SECONDS = 60; // a stream request is renewed after this long
const SEARCH_WORDS = ["review", "letter", "result", "referral", "medicine"]; // plain words, not patient text

export const options = {
  scenarios: {
    experts: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: RAMP, target: EXPERTS },
        { duration: HOLD, target: EXPERTS },
      ],
      gracefulRampDown: "30s",
    },
    ops: { executor: "constant-vus", vus: 1, duration: `${seconds(RAMP) + seconds(HOLD)}s`, exec: "ops" },
  },
  thresholds: {
    "http_req_duration{kind:api}": ["p(95)<=400"],
    "http_req_duration{kind:reader_page}": ["p(95)<=300"],
    server_errors: ["rate<0.001"],
    busy_503: ["rate<0.005"],
    loop_lag_ms_p99: ["max<=100"],
    sign_in_failures: ["count==0"],
  },
};

let signedIn = false;
let streamOpenedAt = 0;
let stream = null;
let lastPage = 0;
let lastSearch = 0;
let lastTimeline = 0;

function holdStream(caseId) {
  const now = Date.now();
  if (stream !== null && now - streamOpenedAt < STREAM_SECONDS * 1000) return;
  streamOpenedAt = now;
  stream = http.asyncRequest("GET", `${BASE}/v1/cases/${caseId}/events`, null, {
    headers: { accept: "text/event-stream" },
    timeout: `${STREAM_SECONDS + 5}s`,
    responseType: "none",
    tags: { name: "case_events", kind: "stream" },
  });
}

function readOnePage(caseId) {
  const n = 1 + Math.floor(Math.random() * PAGES);
  const base = `/v1/cases/${caseId}/pages/${n}`;
  call("GET", base, { route: "reader_page", kind: "reader_page" });
  call("GET", `${base}/image`, { route: "reader_image", kind: "reader_page" });
  call("POST", `${base}/read`, { route: "reader_mark_read" });
  call("GET", `${base}/facts`, { route: "reader_facts", kind: "reader_page" });
}

export default function () {
  const user = userAt(__VU);
  if (!signedIn) signedIn = signIn(user);
  if (!signedIn) {
    sleep(10);
    return;
  }
  const now = Date.now();
  holdStream(user.case_id);
  if (now - lastPage >= PAGE_EVERY_MS) {
    lastPage = now;
    readOnePage(user.case_id);
  }
  if (now - lastSearch >= SEARCH_EVERY_MS) {
    lastSearch = now;
    call("POST", `/v1/cases/${user.case_id}/search`, {
      route: "search",
      json: { q: SEARCH_WORDS[Math.floor(Math.random() * SEARCH_WORDS.length)] },
    });
  }
  if (now - lastTimeline >= TIMELINE_EVERY_MS) {
    lastTimeline = now;
    call("GET", `/v1/cases/${user.case_id}/timeline`, { route: "timeline" });
  }
  sleep(5);
}

// One extra user reads the API's own loop-lag number every 15 seconds for the whole run.
export function ops() {
  scrapeMetrics();
  sleep(15);
}
