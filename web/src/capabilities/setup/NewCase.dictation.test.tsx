// S04 oral instructions can be dictated. Audio goes only to FastAPI's WebSocket (rule 4); the words that come
// back land in the box and the socket is closed when dictation stops. WebSocket, the microphone and
// MediaRecorder are stubbed.
import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { caseItem, casePack, inCase } from "./testing";

class FakeSocket {
  static last: FakeSocket;
  readyState = 0;
  sent: unknown[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeSocket.last = this;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  say(message: object) {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
  }
  send(data: unknown) {
    this.sent.push(data);
  }
  close() {
    this.closed = true;
    this.readyState = 3;
  }
}
class FakeRecorder {
  state = "inactive";
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  start() {
    this.state = "recording";
  }
  stop() {
    this.state = "inactive";
  }
}

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }) },
  });
});
afterEach(() => vi.unstubAllGlobals());

test("dictated words land in the oral instructions box and the socket closes when dictation ends", async () => {
  signedIn({ pack: casePack("launch") });
  inCase("launch");
  server.use(
    http.get("/v1/cases/:caseId", () => HttpResponse.json(caseItem("launch", { due_date: null }), { headers: { etag: '"3"' } })),
    http.get("/v1/cases/:caseId/instructions", () => json([])),
    http.get("/v1/cases/:caseId/parties", () => json([])),
    http.get("/v1/cases/:caseId/instructing-parties", () => json({ current: [], history: [], joint: false })),
    http.get("/v1/cases/:caseId/prefill", () => json({ reading: false, rejected: 0, suggestions: [] })),
  );
  const { user } = renderAt(`/cases/${CASE_A}/details`);
  const oral = await screen.findByRole("region", { name: "Were you told anything by phone that isn't in the letter?" });
  const box = within(oral).getByLabelText("What you were told");
  await user.type(box, "Start.");

  await user.click(within(oral).getByRole("button", { name: "Dictate" }));
  await waitFor(() => expect(FakeSocket.last).toBeDefined());
  const ws = FakeSocket.last;
  expect(ws.url).toContain(`/v1/cases/${CASE_A}/dictation`);
  act(() => ws.open());
  await user.click(await within(oral).findByRole("button", { name: "Stop" }));
  expect(ws.sent).toContain(JSON.stringify({ type: "end" }));

  act(() => ws.say({ type: "segment", text: "Concentrate on May." }));
  await waitFor(() => expect(box).toHaveValue("Start. Concentrate on May."));
  act(() => ws.say({ type: "done" }));
  expect(ws.closed).toBe(true);
  expect(within(oral).getByRole("button", { name: "Dictate" })).toBeEnabled();
});
