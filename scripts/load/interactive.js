// Scenario 2: short jobs a person is waiting for, while bundles run.
//
//   k6 run -e LOAD_INTERACTIVE_OFFSET=60 scripts/load/interactive.js
//
// Every 30 seconds each virtual user queues one short job on its own case (rerun_checks, build_report,
// build_trial_pack or export_case, one kind per user) and measures the seconds until the job's `started_at` is set.
// Pass: p95 of `time_to_start_seconds` at most 30.
//
// Use accounts that are NOT running a bundle (LOAD_INTERACTIVE_OFFSET skips the first accounts in the file) and whose
// cases already have a processed bundle. `build_trial_pack` answers 409 until the case has a served report and
// `export_case` needs a recent second factor; both are counted as `interactive_skipped`, not as failures.
import { sleep } from "k6";
import { Counter, Trend } from "k6/metrics";
import { call, intFromEnv, signIn, userAt } from "./lib/common.js";

const KINDS = [
  { name: "rerun_checks", method: "POST", path: (c) => `/v1/cases/${c}/findings/rerun` },
  { name: "build_report", method: "POST", path: (c) => `/v1/cases/${c}/report/build` },
  { name: "build_trial_pack", method: "POST", path: (c) => `/v1/cases/${c}/trial-pack` },
  { name: "export_case", method: "POST", path: (c) => `/v1/cases/${c}/export` },
];
const OFFSET = intFromEnv("LOAD_INTERACTIVE_OFFSET", 0);
const USERS = intFromEnv("LOAD_INTERACTIVE_USERS", KINDS.length);
const DURATION = __ENV.LOAD_DURATION || "90m";
const START_TIMEOUT_SECONDS = 120;

export const timeToStart = new Trend("time_to_start_seconds");
export const skipped = new Counter("interactive_skipped");
export const neverStarted = new Counter("interactive_never_started");

export const options = {
  scenarios: {
    interactive: { executor: "constant-vus", vus: USERS, duration: DURATION },
  },
  thresholds: {
    time_to_start_seconds: ["p(95)<=30"],
    interactive_never_started: ["count==0"],
    server_errors: ["rate<0.001"],
  },
};

let signedIn = false;

export default function () {
  const user = userAt(OFFSET + __VU);
  if (!signedIn) signedIn = signIn(user);
  if (!signedIn) {
    sleep(30);
    return;
  }
  const kind = KINDS[(__VU - 1) % KINDS.length];
  const queued = call(kind.method, kind.path(user.case_id), { route: `interactive_${kind.name}` });
  if (queued.status === 409 || queued.status === 403) {
    skipped.add(1);
    sleep(30);
    return;
  }
  if (queued.status !== 202) {
    sleep(30);
    return;
  }
  const jobId = queued.json().job_id;
  const began = Date.now();
  let started = false;
  while (Date.now() - began < START_TIMEOUT_SECONDS * 1000) {
    const res = call("GET", `/v1/jobs/${jobId}`, { route: "job_status" });
    if (res.status === 200 && res.json().started_at) {
      timeToStart.add((Date.now() - began) / 1000);
      started = true;
      break;
    }
    sleep(1);
  }
  if (!started) neverStarted.add(1);
  const used = (Date.now() - began) / 1000;
  if (used < 30) sleep(30 - used);
}
