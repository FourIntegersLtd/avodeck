// Scenario 1: N bundles processing at once (5, then 20, then 50).
//
//   k6 run -e LOAD_BUNDLES=5 scripts/load/bundles.js
//
// Each virtual user signs in as its own synthetic expert, uploads the same stitched synthetic bundle to its own empty
// staging case in chunks, waits for the document to be accepted, then follows the case's processing job until it
// ends. Pass: p90 of `bundle_minutes` at most 75, no failed job. Worker memory, 429 retries and double runs are read
// from the API's metrics and the job table, not here (see docs/ops/capacity.md).
//
// The bundle file is read into memory by every virtual user: plan for LOAD_BUNDLES x file size of RAM on the machine
// running k6, or split the users across several machines with --execution-segment.
import { check, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";
import crypto from "k6/crypto";
import { call, intFromEnv, signIn, userAt, userCount } from "./lib/common.js";

const BUNDLES = intFromEnv("LOAD_BUNDLES", 5);
if (BUNDLES < 1 || BUNDLES > 50) throw new Error("LOAD_BUNDLES must be between 1 and 50.");
if (BUNDLES > userCount()) throw new Error("LOAD_USERS_FILE has fewer accounts than LOAD_BUNDLES.");
for (const name of ["LOAD_BUNDLE_FILE", "LOAD_BUNDLE_SHA256", "LOAD_DOCUMENT_KIND"]) {
  if (!__ENV[name]) throw new Error(`${name} is not set (see scripts/load/README.md).`);
}
const BUNDLE = open(__ENV.LOAD_BUNDLE_FILE, "b");
const POLL_SECONDS = 30;
const GIVE_UP_MINUTES = intFromEnv("LOAD_GIVE_UP_MINUTES", 120);

export const bundleMinutes = new Trend("bundle_minutes");
export const failedJobs = new Counter("failed_jobs");
export const timedOut = new Counter("bundles_timed_out");

export const options = {
  scenarios: {
    bundles: {
      executor: "per-vu-iterations",
      vus: BUNDLES,
      iterations: 1,
      maxDuration: `${GIVE_UP_MINUTES + 30}m`,
    },
  },
  thresholds: {
    bundle_minutes: ["p(90)<=75"],
    failed_jobs: ["count==0"],
    bundles_timed_out: ["count==0"],
    server_errors: ["rate<0.001"],
    busy_503: ["rate<0.005"],
  },
};

function upload(caseId) {
  const started = call("POST", `/v1/cases/${caseId}/uploads`, {
    route: "upload_start",
    json: {
      kind: __ENV.LOAD_DOCUMENT_KIND,
      filename: "load-bundle.pdf",
      size: BUNDLE.byteLength,
      sha256: __ENV.LOAD_BUNDLE_SHA256,
    },
  });
  if (!check(started, { "upload started": (r) => r.status === 201 })) return false;
  const info = started.json();
  for (let n = 0; n < info.chunk_count; n++) {
    const part = BUNDLE.slice(n * info.chunk_size, Math.min(BUNDLE.byteLength, (n + 1) * info.chunk_size));
    const sent = call("PUT", `/v1/uploads/${info.upload_id}/chunks/${n}`, {
      route: "upload_chunk",
      body: part,
      headers: {
        "content-type": "application/octet-stream",
        "x-chunk-sha256": crypto.sha256(part, "hex"),
      },
      timeout: "300s",
    });
    if (!check(sent, { "chunk stored": (r) => r.status === 200 })) return false;
  }
  for (let attempt = 0; attempt < 120; attempt++) {
    const done = call("POST", `/v1/uploads/${info.upload_id}/complete`, {
      route: "upload_complete",
      json: { sha256: __ENV.LOAD_BUNDLE_SHA256 },
    });
    if (done.status === 200) return true;
    if (done.status !== 202) return false;
    sleep(5);
  }
  return false;
}

export default function () {
  const user = userAt(__VU);
  if (!signIn(user)) return;
  const began = Date.now();
  if (!upload(user.case_id)) {
    failedJobs.add(1);
    return;
  }
  if (__ENV.LOAD_START_PROCESSING === "1") {
    call("POST", `/v1/cases/${user.case_id}/process`, { route: "process_start" });
  }
  while ((Date.now() - began) / 60000 < GIVE_UP_MINUTES) {
    sleep(POLL_SECONDS);
    const res = call("GET", `/v1/cases/${user.case_id}/process`, { route: "process_status" });
    if (res.status !== 200) continue;
    const job = res.json().job;
    if (job && job.kind === "process_bundle" && job.status === "failed") {
      failedJobs.add(1);
      return;
    }
    if (job && job.kind === "process_bundle" && job.status === "done") {
      bundleMinutes.add((Date.now() - began) / 60000);
      return;
    }
  }
  timedOut.add(1);
}
