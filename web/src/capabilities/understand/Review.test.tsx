// Slice D audit (docs/slices/D-understand.md): buttons follow what the person may do on the case
// (Case.allowed_actions), decided findings no longer found stay listed, each check's number of days can be
// changed, S13 reviews the links between entries and questions, and S12 can show any day, keeps the note
// private and says when guidance has changed since the case first saw it.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { ASSISTANT_ACTIONS, consultation, event, FACT_1, finding, inCase, link, Q_2, timeline, type PackRun } from "./testing";

type Call = { method: string; url: URL; body: unknown; ifMatch: string | null };

function serveThings(links = [link()], findings = [finding()]) {
  const calls: Call[] = [];
  const record = async (request: Request) => {
    const body = request.method === "GET" ? null : await request.json().catch(() => null);
    calls.push({ method: request.method, url: new URL(request.url), body, ifMatch: request.headers.get("if-match") });
  };
  server.use(
    http.get("/v1/cases/:caseId/findings", () => json({ findings })),
    http.get("/v1/me/checks", () =>
      json({ checks: [{ key: "abnormal_result_no_action", rule: "no_action_within", gentle: false, default_on: true, enabled: true, window_days: 14 }] }),
    ),
    http.patch("/v1/me/checks", async ({ request }) => {
      await record(request);
      return json({ check_key: "abnormal_result_no_action", enabled: true, window_days: 21 });
    }),
    http.get("/v1/cases/:caseId/fact-links", () => json({ links })),
    http.post("/v1/cases/:caseId/fact-links/suggest", async ({ request }) => {
      await record(request);
      return json({ job_id: "e1111111-1111-4111-8111-111111111111" }, 202);
    }),
    http.patch("/v1/cases/:caseId/fact-links/:id", async ({ request }) => {
      await record(request);
      const sent = calls.at(-1)!.body as { status: string };
      return json(link({ status: sent.status, version: 2 }));
    }),
    http.post("/v1/cases/:caseId/fact-links", async ({ request }) => {
      await record(request);
      return json(link({ source: "expert", status: "confirmed", question_id: Q_2, fact_id: FACT_1 }), 201);
    }),
    http.get("/v1/cases/:caseId/timeline", ({ request }) => {
      calls.push({ method: "GET", url: new URL(request.url), body: null, ifMatch: null });
      return json(timeline());
    }),
  );
  return calls;
}
const things = () => renderAt(`/cases/${CASE_A}/things-to-look-at`);

// --- role gating -----------------------------------------------------------------------------------------

test("someone who may not decide sees the findings and links but no decide, check-again or confirm buttons", async () => {
  inCase("launch", ASSISTANT_ACTIONS);
  serveThings();
  things();
  await screen.findByRole("article", { name: "Nothing in the record shows what followed this entry" });
  const links = await screen.findByRole("region", { name: "Links to the questions" });
  await within(links).findByText("Question 1");
  expect(screen.queryByRole("button", { name: "Save my decision" })).not.toBeInTheDocument();
  expect(screen.queryByRole("radio", { name: "Relevant" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Check again" })).not.toBeInTheDocument();
  expect(within(links).queryByRole("button", { name: "Confirm link" })).not.toBeInTheDocument();
  expect(within(links).queryByRole("button", { name: "Add link" })).not.toBeInTheDocument();
  expect(within(links).getByRole("button", { name: "Suggest links" })).toBeInTheDocument();
});

test("someone who may not add notes or change arrivals sees neither on a consultation", async () => {
  inCase("launch", ASSISTANT_ACTIONS);
  server.use(http.get("/v1/cases/:caseId/consultations/:factId", () => json(consultation())));
  renderAt(`/cases/${CASE_A}/consultation/${FACT_1}`);
  await screen.findByRole("heading", { level: 2, name: "In the record that day" });
  expect(screen.queryByRole("button", { name: "Save note" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Change when this arrived" })).not.toBeInTheDocument();
});

// --- decided findings no longer found and each check's days -----------------------------------------------

test("a decided finding that is no longer found stays listed and says so", async () => {
  inCase("launch");
  serveThings([], [finding({ status: "gone", decision: "relevant" })]);
  things();
  const card = await screen.findByRole("article", { name: "Nothing in the record shows what followed this entry" });
  expect(within(card).getByText("No longer found when we checked again. Your decision is kept.")).toBeInTheDocument();
});

test("the number of days a check allows can be changed", async () => {
  inCase("launch");
  const calls = serveThings();
  const { user } = things();
  const checks = await screen.findByRole("region", { name: "The checks we run" });
  const days = await within(checks).findByLabelText("Days allowed: Abnormal result no action");
  expect(days).toHaveValue(14);
  await user.clear(days);
  await user.type(days, "21");
  await user.click(within(checks).getByRole("button", { name: "Save days" }));
  await waitFor(() =>
    expect(calls.find((c) => c.url.pathname === "/v1/me/checks")?.body).toEqual({ check_key: "abnormal_result_no_action", window_days: 21 }),
  );
});

// --- reviewing links ---------------------------------------------------------------------------------------

describe.each<PackRun>(["launch", "fixture"])("links under the %s packs", (run) => {
  test("each suggested link names its question and entry, and passes axe", async () => {
    inCase(run);
    serveThings([link(), link({ id: "b2222222-2222-4222-8222-222222222222", status: "stale", question_id: null })]);
    const { container } = things();
    const links = await screen.findByRole("region", { name: "Links to the questions" });
    const suggested = await within(links).findByRole("listitem", { name: /Question 1/ });
    expect(within(suggested).getByText("Result · 19 February 2024 · p.214")).toBeInTheDocument();
    expect(within(links).getByRole("heading", { name: "Links that no longer hold" })).toBeInTheDocument();
    expect(within(links).getByText("The question was taken away, or the entry was marked not right.")).toBeInTheDocument();
    expect(await axeViolations(container)).toEqual([]);
  });
});

test.each([["Confirm link", "confirmed"], ["Not linked", "rejected"]])("'%s' sends the decision with the version it saw", async (button, status) => {
  inCase("launch");
  const calls = serveThings();
  const { user } = things();
  const links = await screen.findByRole("region", { name: "Links to the questions" });
  await user.click(await within(links).findByRole("button", { name: button }));
  await waitFor(() => expect(calls.find((c) => c.method === "PATCH" && c.url.pathname.includes("fact-links"))).toBeTruthy());
  const sent = calls.find((c) => c.method === "PATCH" && c.url.pathname.includes("fact-links"))!;
  expect(sent.body).toEqual({ status });
  expect(sent.ifMatch).toBe('"1"');
});

test("asking for suggestions starts the job and says so", async () => {
  inCase("launch");
  const calls = serveThings([]);
  const { user } = things();
  const links = await screen.findByRole("region", { name: "Links to the questions" });
  await user.click(within(links).getByRole("button", { name: "Suggest links" }));
  await waitFor(() => expect(calls.some((c) => c.url.pathname.endsWith("/fact-links/suggest"))).toBe(true));
  expect(await within(links).findByText("We're looking for links. Suggestions will appear here for you to check.")).toBeInTheDocument();
});

test("a link can be added by hand between a checked entry and a question", async () => {
  inCase("launch");
  const calls = serveThings([]);
  const { user } = things();
  const links = await screen.findByRole("region", { name: "Links to the questions" });
  const entry = await within(links).findByLabelText("Entry");
  await waitFor(() => expect(calls.some((c) => c.url.pathname.endsWith("/timeline") && c.url.searchParams.get("status") === "verified")).toBe(true));
  await user.selectOptions(entry, await within(links).findByRole("option", { name: "Consultation · 15 May 2024 · p.118" }));
  await user.selectOptions(within(links).getByLabelText("Question"), within(links).getByRole("option", { name: "Question 2" }));
  await user.click(within(links).getByRole("button", { name: "Add link" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.url.pathname.endsWith("/fact-links"))?.body).toEqual({ fact_id: FACT_1, question_id: Q_2 }));
});

// --- one consultation: any day, a private note, changed guidance --------------------------------------------

test("the record can be shown as it stood on another day", async () => {
  inCase("launch");
  const asked: URL[] = [];
  server.use(
    http.get("/v1/cases/:caseId/consultations/:factId", ({ request }) => {
      const url = new URL(request.url);
      asked.push(url);
      return json(consultation({ on: url.searchParams.get("on") ?? "2024-05-15" }));
    }),
  );
  const { user } = renderAt(`/cases/${CASE_A}/consultation/${FACT_1}`);
  const day = await screen.findByLabelText("Show the record as it stood on");
  expect(day).toHaveValue("2024-05-15");
  await user.clear(day);
  await user.type(day, "2024-05-23");
  await waitFor(() => expect(asked.some((u) => u.searchParams.get("on") === "2024-05-23")).toBe(true));
});

test("the note says only you see it", async () => {
  inCase("launch");
  server.use(http.get("/v1/cases/:caseId/consultations/:factId", () => json(consultation())));
  renderAt(`/cases/${CASE_A}/consultation/${FACT_1}`);
  expect(await screen.findByText("Only you see this note. It never goes into your report.")).toBeInTheDocument();
});

test("guidance changed since the case first saw it shows a notice on the consultation and the timeline", async () => {
  inCase("launch");
  server.use(
    http.get("/v1/cases/:caseId/consultations/:factId", () => json(consultation({ guidance_changed: ["G1"] }))),
    http.get("/v1/cases/:caseId/timeline", () => json(timeline({ guidance_changed: ["G1"], lanes: [{ key: "l", label: "L", events: [event()] }] }))),
  );
  renderAt(`/cases/${CASE_A}/consultation/${FACT_1}`);
  const notice = "The guidance list has changed since this case first showed it (G1). Check again to use the new list.";
  expect(await screen.findByText(notice)).toBeInTheDocument();
  renderAt(`/cases/${CASE_A}/timeline`);
  expect(await screen.findAllByText(notice)).not.toHaveLength(0);
});
