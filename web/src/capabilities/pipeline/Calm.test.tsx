// The pipeline screens in the calm look (DesignSystemCalm board): one green primary button per screen,
// other actions quiet or secondary, rare actions in a Menu, borderless amber notices, a segmented choice.
import { screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { launchPack } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { CASE_ID, draft, EXPERT_ACTIONS, missingItem, pageId, processingJob, summary, THUMB, theCase } from "./testData";

const pack = launchPack;
const VARIANT = /av-button--(secondary|outline|quiet|link|danger)/;
const primaries = (container: HTMLElement) => Array.from(container.querySelectorAll<HTMLElement>(".av-button")).filter((el) => !VARIANT.test(el.className));

function mockCase(handlers: Parameters<typeof server.use>) {
  signedIn({ pack });
  server.use(
    http.get(`/v1/cases/${CASE_ID}`, () => json(theCase({ allowed_actions: EXPERT_ACTIONS }))),
    http.get(`/v1/cases/${CASE_ID}/pages/:pageNo/thumbnail`, () => new HttpResponse(THUMB, { headers: { "Content-Type": "image/jpeg" } })),
    ...handlers,
  );
}

describe("Bundle ready, calm", () => {
  test("one primary button, and amber items are borderless notices", async () => {
    mockCase([
      http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () => json(summary(pack))),
      http.get(`/v1/cases/${CASE_ID}/missing-items`, () => json({ missing_items: [missingItem()] })),
    ]);
    const { container } = renderAt(`/cases/${CASE_ID}/bundle`);
    await screen.findByRole("heading", { level: 1, name: "Your bundle is ready" });
    expect(primaries(container)).toHaveLength(1);
    expect(container.querySelectorAll(".av-notice--amber").length).toBeGreaterThan(0);
    expect(container.querySelector(".pl-card--amber")).toBeNull();
  });
});

describe("Preparing, calm", () => {
  test("while running there is no primary button and Stop sits in a Menu", async () => {
    mockCase([
      http.get(`/v1/cases/${CASE_ID}/process`, () => json({ job: processingJob() })),
      http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () => json(summary(pack))),
    ]);
    const { container, user } = renderAt(`/cases/${CASE_ID}/preparing`);
    await screen.findByRole("heading", { level: 1 });
    expect(primaries(container)).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Stop preparing" })).toBeNull();
    await user.click(await screen.findByRole("button", { name: "More" }));
    expect(screen.getByRole("button", { name: "Stop preparing" })).toBeVisible();
  });

  test("with nothing started, Start preparing is the one primary button", async () => {
    mockCase([
      http.get(`/v1/cases/${CASE_ID}/process`, () => json({ job: null })),
      http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () => json(summary(pack))),
    ]);
    const { container } = renderAt(`/cases/${CASE_ID}/preparing`);
    await screen.findByRole("button", { name: "Start preparing" });
    expect(primaries(container)).toHaveLength(1);
  });
});

describe("Ask for what's missing, calm", () => {
  test("Send is the one primary button and Copy is in a Menu", async () => {
    mockCase([
      http.post(`/v1/cases/${CASE_ID}/instructing-party-requests/draft`, () => json(draft({ label: "Request" }))),
      http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () => json(summary(pack))),
    ]);
    const { container, user } = renderAt(`/cases/${CASE_ID}/bundle/ask`);
    await screen.findByRole("button", { name: "Edit the email" });
    expect(primaries(container)).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Copy the text" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "More" }));
    expect(screen.getByRole("button", { name: "Copy the text" })).toBeVisible();
  });
});

describe("Not this patient's record, calm", () => {
  test("each page has a segmented choice and the submit button is the one primary", async () => {
    const s = (n: number): Schemas["QuarantinePage"] => ({ page_id: pageId(n), page_no: n, status: "suggested", reason: "identifiers_differ", job_id: null });
    mockCase([http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () => json(summary(pack, { quarantine_suggestions: [412, 413].map(s), quarantined: [] })))]);
    const { container } = renderAt(`/cases/${CASE_ID}/bundle/not-this-patient`);
    await screen.findByRole("heading", { level: 1, name: "These pages may belong to someone else" });
    expect(container.querySelectorAll(".av-segmented-control")).toHaveLength(2);
    expect(primaries(container)).toHaveLength(1);
  });
});
