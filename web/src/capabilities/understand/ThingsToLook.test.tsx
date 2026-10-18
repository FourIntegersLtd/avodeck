// S13 Things to look at: open findings with the pages they rest on, gentler words for gentle checks, a decision
// on each (a reason is required for "not relevant" and "explained elsewhere"), checking again, and the checks you use.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { finding, inCase, launchWords, type PackRun } from "./testing";

const second = finding({ id: "c2222222-2222-4222-8222-222222222222", check_key: "referral_acknowledged", rule: "not_acknowledged_within", evidence_pages: [1071, 1072], decision: "relevant", reason: null });
const gentle = finding({ id: "c3333333-3333-4333-8333-333333333333", check_key: "zz_session_length_recorded", rule: "no_action_within", gentle: true, evidence_pages: [9] });

function serve(list = [finding(), second]) {
  const calls: Array<{ method: string; url: URL; body: unknown }> = [];
  server.use(
    http.get("/v1/cases/:caseId/findings", () => json({ findings: list })),
    http.patch("/v1/cases/:caseId/findings/:id", async ({ request, params }) => {
      const sent = (await request.json()) as { decision: string; reason?: string | null };
      calls.push({ method: "PATCH", url: new URL(request.url), body: sent });
      const base = list.find((f) => f.id === params.id)!;
      return json({ ...base, decision: sent.decision, reason: sent.reason ?? null, version: base.version + 1 });
    }),
    http.post("/v1/cases/:caseId/findings/rerun", () => {
      calls.push({ method: "POST", url: new URL("http://x/rerun"), body: null });
      return json({ job_id: "e1111111-1111-4111-8111-111111111111" }, 202);
    }),
    http.get("/v1/me/checks", () =>
      json({ checks: [{ key: "abnormal_result_no_action", rule: "no_action_within", gentle: false, default_on: true, enabled: true, window_days: 14 }] }),
    ),
    http.patch("/v1/me/checks", async ({ request }) => {
      const sent = (await request.json()) as { check_key: string; enabled: boolean };
      calls.push({ method: "PATCH", url: new URL(request.url), body: sent });
      return json({ check_key: sent.check_key, enabled: sent.enabled, window_days: 14 });
    }),
  );
  return calls;
}
const open = () => renderAt(`/cases/${CASE_A}/things-to-look-at`);

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("lists each thing to look at with its pages, how many you have decided, and passes axe", async () => {
    inCase(run);
    serve();
    const { container } = open();
    expect(await screen.findByRole("heading", { level: 2, name: "Things to look at" })).toBeInTheDocument();
    expect(screen.getByText("These are prompts, not findings. You decide.", { exact: false })).toBeInTheDocument();
    expect(await screen.findByText("1 of 2 decided")).toBeInTheDocument();
    const first = screen.getByRole("article", { name: "Nothing in the record shows what followed this entry" });
    expect(within(first).getByText("p.214")).toBeInTheDocument();
    const other = screen.getByRole("article", { name: "Nothing in the record shows this was acknowledged" });
    expect(within(other).getByText("p.1071, p.1072")).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("a gentle check uses the softer wording", async () => {
  inCase("launch");
  serve([gentle]);
  open();
  expect(await screen.findByRole("article", { name: "You may want to check what followed this entry" })).toBeInTheDocument();
});

test("'relevant' is saved without a reason", async () => {
  inCase("launch");
  const calls = serve();
  const { user } = open();
  const card = await screen.findByRole("article", { name: "Nothing in the record shows what followed this entry" });
  await user.click(within(card).getByRole("radio", { name: "Relevant" }));
  await user.click(within(card).getByRole("button", { name: "Save my decision" }));
  await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ decision: "relevant", reason: null }));
  expect(await screen.findByText("2 of 2 decided")).toBeInTheDocument();
});

test.each([["Not relevant", "not_relevant"], ["Explained elsewhere", "explained_elsewhere"]])("'%s' needs a reason before it can be saved", async (label, decision) => {
  inCase("launch");
  const calls = serve();
  const { user } = open();
  const card = await screen.findByRole("article", { name: "Nothing in the record shows what followed this entry" });
  await user.click(within(card).getByRole("radio", { name: label }));
  const save = within(card).getByRole("button", { name: "Save my decision" });
  expect(save).toBeDisabled();
  await user.type(within(card).getByLabelText("Your reason"), "The ward saw it the same day");
  await user.click(save);
  await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ decision, reason: "The ward saw it the same day" }));
});

test("checking again starts the checks and says what happens next", async () => {
  inCase("launch");
  const calls = serve();
  const { user } = open();
  await user.click(await screen.findByRole("button", { name: "Check again" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
  expect(await screen.findByText("We're checking again. New things may appear in a moment. Your decisions are kept.")).toBeInTheDocument();
});

test("you can switch one of your checks off for the next time", async () => {
  inCase("launch");
  const calls = serve();
  const { user } = open();
  const checks = await screen.findByRole("region", { name: "Your checks" });
  expect(within(checks).getByText("Changes apply the next time we check.")).toBeInTheDocument();
  await user.click(await within(checks).findByRole("checkbox", { name: "Abnormal result no action" }));
  await waitFor(() => expect(calls.find((c) => c.url.pathname === "/v1/me/checks")?.body).toEqual({ check_key: "abnormal_result_no_action", enabled: false }));
});
