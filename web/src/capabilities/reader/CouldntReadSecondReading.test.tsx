// Screen 10, another reading of a page (T-10): asked for by the expert, shown beside the page to check, never merged.
import { act, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { forgetPages } from "../../foundation/lib/pageImageCache";
import { http, json, server } from "../../test/api";
import { FakeEventSource, liveJob } from "../../test/eventSource";
import { axeViolations, renderAt } from "../../test/render";
import { launchPack } from "../../test/fixtures";
import { CASE_ID } from "./testData";
import { mockReader } from "./testing";
import "./index";

beforeEach(() => {
  forgetPages();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:page"), revokeObjectURL: vi.fn() }));
});

const PATH = `/cases/${CASE_ID}/read/couldnt-read`;

describe("another reading", () => {
  test("asking sends the request and shows the lines to check against the page, with no merge button", async () => {
    const api = mockReader(launchPack, {
      secondReading: { source: "machine_read", status: "done", reason: null, lines: [{ text: "Seen today chest clear", confidence: 0.7 }] },
    });
    const { user, container } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Ask for another reading" }));
    expect(await screen.findByText("Another reading (check it against the page)")).toBeVisible();
    expect(await screen.findByText("Seen today chest clear")).toBeVisible();
    expect(api.secondReadingAsks).toEqual([{ page: 612, query: "?on_request=true" }]);
    expect(screen.queryByRole("button", { name: /use|merge|replace|insert/i })).toBeNull();
    expect(await axeViolations(container)).toEqual([]);
  });

  test("a queued reading shows its lines when the case stream says it is done, with no refresh", async () => {
    mockReader(launchPack, { secondReading: { source: "machine_read", status: "queued", reason: null, lines: [] } });
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Ask for another reading" }));
    await waitFor(() => expect(FakeEventSource.open()).toHaveLength(1));
    act(() => FakeEventSource.open()[0]!.emitJobs([liveJob({ id: "sr", kind: "second_reading", status: "running" })]));
    server.use(http.get("/v1/cases/:caseId/pages/:pageNo/second-reading", () => json({ status: "done", reason: null, lines: [{ text: "Seen today chest clear", confidence: 0.7 }] })));
    act(() => FakeEventSource.open()[0]!.emitJobs([liveJob({ id: "sr", kind: "second_reading", status: "done" })])); // a later change, not the first snapshot
    expect(await screen.findByText("Seen today chest clear")).toBeVisible();
  });

  test("a refusal says nothing was sent", async () => {
    mockReader(launchPack, { secondReading: { source: "machine_read", status: "refused", reason: "low_confidence", lines: [] } });
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Ask for another reading" }));
    expect(await screen.findByText("We couldn't hide the name safely on this page, so we didn't send it.")).toBeVisible();
  });

  test("a failed reading offers a plain message", async () => {
    mockReader(launchPack, { secondReading: { source: "machine_read", status: "failed", reason: "invalid_output", lines: [] } });
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Ask for another reading" }));
    expect(await screen.findByText("We couldn't get another reading just now. Please try again.")).toBeVisible();
  });
});
