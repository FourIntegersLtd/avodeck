// The reusable dictation box: microphone audio goes to the API's WebSocket only (rule 4), the
// text comes back to the box, and the expert can always type instead.
import { screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { renderUi } from "../../test/render";
import { DictationBox } from ".";

class FakeSocket {
  static last: FakeSocket | null = null;
  url: string;
  binaryType = "blob";
  sent: Array<string | Blob> = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeSocket.last = this;
    queueMicrotask(() => {
      this.readyState = 1;
      this.onopen?.();
    });
  }
  send(data: string | Blob) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
    this.onclose?.({ code: 1000 });
  }
  receive(message: object) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
}

class FakeRecorder {
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  state = "inactive";
  constructor(public stream: unknown) {}
  start() {
    this.state = "recording";
    this.ondataavailable?.({ data: new Blob(["audio"]) });
  }
  stop() {
    this.state = "inactive";
    this.onstop?.();
  }
}

const getUserMedia = vi.fn();

beforeEach(() => {
  FakeSocket.last = null;
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  getUserMedia.mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] });
  Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
});
afterEach(() => vi.unstubAllGlobals());

function Harness() {
  const [text, setText] = useState("Seen on 3 May.");
  return <DictationBox caseId="c1" context="opinion" label="Your opinion" value={text} onChange={setText} />;
}

test("dictating streams audio to the API and adds the words to the box", async () => {
  const { user } = renderUi(<Harness />);
  await user.click(screen.getByRole("button", { name: "Dictate" }));
  await waitFor(() => expect(FakeSocket.last?.readyState).toBe(1));
  const socket = FakeSocket.last!;
  expect(socket.url).toBe(`ws://${window.location.host}/v1/cases/c1/dictation?context=opinion`);
  expect(screen.getByText("Listening…")).toBeInTheDocument();
  await waitFor(() => expect(socket.sent.some((d) => d instanceof Blob)).toBe(true));

  await user.click(screen.getByRole("button", { name: "Stop" }));
  expect(socket.sent).toContain(JSON.stringify({ type: "end" }));
  socket.receive({ type: "segment", text: "The plan was reasonable.", start_ms: 0, end_ms: 900 });
  socket.receive({ type: "done" });
  await waitFor(() => expect(screen.getByLabelText("Your opinion")).toHaveValue("Seen on 3 May. The plan was reasonable."));
  expect(screen.getByRole("button", { name: "Dictate" })).toBeInTheDocument();
});

test("when the speech service is down the expert is told to type instead", async () => {
  const { user } = renderUi(<Harness />);
  await user.click(screen.getByRole("button", { name: "Dictate" }));
  await waitFor(() => expect(FakeSocket.last?.readyState).toBe(1));
  FakeSocket.last!.receive({ type: "error", code: "speech_unavailable" });
  expect(await screen.findByText("Dictation isn't working just now. You can type instead.")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Your opinion"), " Typed.");
  expect(screen.getByLabelText("Your opinion")).toHaveValue("Seen on 3 May. Typed.");
});

test("if the microphone is refused the expert can still type", async () => {
  getUserMedia.mockRejectedValueOnce(new DOMException("denied", "NotAllowedError"));
  const { user } = renderUi(<Harness />);
  await user.click(screen.getByRole("button", { name: "Dictate" }));
  expect(await screen.findByText("We can't use your microphone. You can type instead.")).toBeInTheDocument();
  expect(FakeSocket.last).toBeNull();
});
