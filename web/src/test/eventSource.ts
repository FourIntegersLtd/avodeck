// A stand-in EventSource for tests (jsdom has none). The live hub opens one per case; a test sends a
// case's jobs with `emitJobs`, the way GET /v1/cases/{id}/events does. Nothing arrives unless a test sends it.
export class FakeEventSource {
  static all: FakeEventSource[] = [];
  onmessage: ((e: MessageEvent<string>) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  readyState = 1;
  closed = false;
  constructor(
    public url: string,
    public init?: { withCredentials?: boolean },
  ) {
    FakeEventSource.all.push(this);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  emitJobs(jobs: object[]) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify({ jobs }) }));
  }
  static open(): FakeEventSource[] {
    return FakeEventSource.all.filter((s) => !s.closed);
  }
}

/** A job as the case stream sends it, with the given fields changed. */
export const liveJob = (over: Record<string, unknown> = {}) => ({
  id: "77777777-7777-4777-8777-777777777777",
  kind: "process_bundle",
  status: "running",
  step: "sort",
  pct: 48,
  eta_seconds: null,
  step_done: null,
  step_total: null,
  error_code: null,
  ...over,
});
