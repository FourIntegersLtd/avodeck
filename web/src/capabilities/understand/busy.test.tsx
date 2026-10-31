// Every action on S12 and S13 shows it is working and ignores a second click; every reload behind content that
// is already on screen says it is updating (the old content stays, dimmed and marked busy).
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { http, HttpResponse, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { byName, expectBusyOnce, hold } from "./busyTesting";
import { forgetPages } from "./pageImage";
import { comment, consultation, entry, FACT_1, FACT_2, FACT_3, finding, inCase, link, Q_2, timeline } from "./testing";

vi.setConfig({ testTimeout: 20_000 });
const png =() => new HttpResponse(new Uint8Array([137, 80, 78, 71]), { headers: { "content-type": "image/png" } });
const DAY = "/v1/cases/:caseId/consultations/:factId";

function serveConsultation(over: Parameters<typeof consultation>[0] = {}) {
  server.use(
    http.get(DAY, () => json(consultation(over))),
    http.get(`${DAY}/later-notes`, () => json({ notes: [] })),
    http.get("/v1/cases/:caseId/pages/:pageNo/image", () => png()),
    http.get("/v1/cases/:caseId/comments", () => json({ comments: [comment()] })),
  );
}
const openConsultation = () => renderAt(`/cases/${CASE_A}/consultation/${FACT_1}`);

// --- S12 comments -------------------------------------------------------------------------------------------

test("adding a comment goes busy and sends once", async () => {
  inCase("launch");
  serveConsultation();
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/comments", async () => (await h.enter(), json(comment(), 201))));
  const { user } = openConsultation();
  const region = await screen.findByRole("region", { name: "Comments" });
  try {
    await user.type(await within(region).findByLabelText("Add a comment"), "Check the letter");
    await expectBusyOnce(user, within(region).getByRole("button", { name: "Add comment" }), h);
  } finally { h.release(); }
});

test("marking a comment as dealt with goes busy and sends once", async () => {
  inCase("launch");
  serveConsultation();
  const h = hold();
  server.use(http.patch("/v1/cases/:caseId/comments/:id", async () => (await h.enter(), json(comment({ resolved_at: "2026-10-03T09:00:00Z" })))));
  const { user } = openConsultation();
  const region = await screen.findByRole("region", { name: "Comments" });
  try {
    await expectBusyOnce(user, await within(region).findByRole("button", { name: "Mark as dealt with" }), h);
  } finally { h.release(); }
});

test("the comments say they are updating while they load again", async () => {
  inCase("launch");
  serveConsultation();
  const h = hold();
  let reload = false;
  server.use(
    http.get("/v1/cases/:caseId/comments", async () => {
      if (reload) await h.enter();
      return json({ comments: [comment()] });
    }),
    http.patch("/v1/cases/:caseId/comments/:id", () => json(comment({ resolved_at: "2026-10-03T09:00:00Z" }))),
  );
  const { user } = openConsultation();
  const region = await screen.findByRole("region", { name: "Comments" });
  const resolve = await within(region).findByRole("button", { name: "Mark as dealt with" });
  reload = true;
  try {
    await user.click(resolve);
    expect(await within(region).findByText("Updating the comments…")).toBeInTheDocument();
    expect(region).toHaveAttribute("aria-busy", "true");
  } finally { h.release(); }
  await waitFor(() => expect(within(region).queryByText("Updating the comments…")).not.toBeInTheDocument());
});

// --- S12 the consultation ---------------------------------------------------------------------------------

test("saving the arrival date goes busy and sends once", async () => {
  inCase("launch");
  serveConsultation();
  const h = hold();
  server.use(http.patch("/v1/cases/:caseId/facts/:factId/availability", async () => (await h.enter(), json({ fact_id: FACT_1, available_at: "2024-05-11", unknown: false }))));
  const { user } = openConsultation();
  const unknown = await screen.findByRole("region", { name: "Not known when this arrived" });
  await user.click(within(unknown).getByRole("button", { name: "Set arrival" }));
  const dialog = await screen.findByRole("dialog", { name: "When did this arrive?" });
  try {
    await user.type(within(dialog).getByLabelText("Date it arrived"), "2024-05-11");
    await user.type(within(dialog).getByLabelText("Why are you changing it?"), "Stamped on the 11th");
    await expectBusyOnce(user, within(dialog).getByRole("button", { name: "Save" }), h);
  } finally { h.release(); }
});

test("saving the note goes busy and sends once", async () => {
  inCase("launch");
  serveConsultation();
  const h = hold();
  server.use(http.put("/v1/cases/:caseId/consultations/:factId/note", async () => (await h.enter(), json({ fact_id: FACT_1, body: "x" }))));
  const { user } = openConsultation();
  try {
    await expectBusyOnce(user, await byName("Save note"), h);
  } finally { h.release(); }
});

test("Save note and continue goes busy and sends once", async () => {
  inCase("launch");
  serveConsultation();
  const h = hold();
  server.use(
    http.put("/v1/cases/:caseId/consultations/:factId/note", async () => (await h.enter(), json({ fact_id: FACT_1, body: "x" }))),
    http.get("/v1/cases/:caseId/findings", () => json({ findings: [] })),
    http.get("/v1/me/checks", () => json({ checks: [] })),
  );
  const { user } = openConsultation();
  try {
    await expectBusyOnce(user, await byName("Save note and continue"), h);
  } finally { h.release(); }
});

test("picking another day says the consultation is updating while the old one stays", async () => {
  inCase("launch");
  serveConsultation();
  const h = hold();
  server.use(
    http.get(DAY, async ({ request }) => {
      if (new URL(request.url).searchParams.get("on")) await h.enter();
      return json(consultation());
    }),
  );
  openConsultation();
  const heading = await screen.findByRole("heading", { level: 2, name: "Consultation · 15 May 2024" });
  try {
    fireEvent.change(screen.getByLabelText("Show the record as it stood on"), { target: { value: "2024-05-20" } });
    expect(await screen.findByText("Updating this consultation…")).toBeInTheDocument();
    expect(heading.closest("[aria-busy]")).toHaveAttribute("aria-busy", "true");
  } finally { h.release(); }
  await waitFor(() => expect(screen.queryByText("Updating this consultation…")).not.toBeInTheDocument());
});

test("the page picture says it is updating while another page loads", async () => {
  inCase("launch");
  forgetPages();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:page"), revokeObjectURL: vi.fn() }));
  serveConsultation();
  const h = hold();
  server.use(
    http.get(DAY, ({ request }) => json(new URL(request.url).searchParams.get("on") ? consultation({ entry: entry({ fact_id: FACT_1, kind: "consultation", page_no: 119 }) }) : consultation())),
    http.get("/v1/cases/:caseId/pages/:pageNo/image", async ({ params }) => {
      if (params.pageNo === "119") await h.enter();
      return png();
    }),
  );
  openConsultation();
  const picture = await screen.findByAltText("Page 118 of the record");
  try {
    fireEvent.change(screen.getByLabelText("Show the record as it stood on"), { target: { value: "2024-05-20" } });
    expect(await screen.findByText("Updating the page…")).toBeInTheDocument();
    expect(picture.closest("[aria-busy]")).toHaveAttribute("aria-busy", "true");
    expect(picture).toBeInTheDocument();
  } finally { h.release(); }
  await waitFor(() => expect(screen.queryByText("Updating the page…")).not.toBeInTheDocument());
});

test("the later entries say they are updating while another consultation's load", async () => {
  inCase("launch");
  serveConsultation();
  const h = hold();
  server.use(
    http.get(`${DAY}/later-notes`, async ({ params }) => {
      if (params.factId === FACT_2) await h.enter();
      return json({ notes: [{ fact_id: FACT_3, kind: "consultation", page_no: 131, event_date: "2024-08-08", available_at: "2024-08-09", has_note: false }] });
    }),
  );
  const { router } = openConsultation();
  const region = await screen.findByRole("region", { name: "Later entries of this kind" });
  await within(region).findByText("p.131");
  try {
    await act(async () => { await router.navigate(`/cases/${CASE_A}/consultation/${FACT_2}`); });
    expect(await within(region).findByText("Updating the later entries…")).toBeInTheDocument();
    expect(region).toHaveAttribute("aria-busy", "true");
  } finally { h.release(); }
});

// --- S13 things to look at ---------------------------------------------------------------------------------

function serveThings() {
  server.use(
    http.get("/v1/cases/:caseId/findings", () => json({ findings: [finding()] })),
    http.get("/v1/me/checks", () =>
      json({ checks: [{ key: "abnormal_result_no_action", rule: "no_action_within", gentle: false, default_on: true, enabled: true, window_days: 14 }] }),
    ),
    http.get("/v1/cases/:caseId/fact-links", () => json({ links: [link()] })),
    http.get("/v1/cases/:caseId/timeline", () => json(timeline())),
  );
}
const openThings = () => renderAt(`/cases/${CASE_A}/things-to-look-at`);
const CARD = "Nothing in the record shows what followed this entry";

test("deciding a finding with a reason goes busy and sends once", async () => {
  inCase("launch");
  serveThings();
  const h = hold();
  server.use(http.patch("/v1/cases/:caseId/findings/:id", async () => (await h.enter(), json(finding({ decision: "not_relevant", reason: "Seen" })))));
  const { user } = openThings();
  const card = await screen.findByRole("article", { name: CARD });
  try {
    await user.click(within(card).getByRole("button", { name: "Not relevant" }));
    await user.type(within(card).getByLabelText("Your reason"), "Seen");
    await expectBusyOnce(user, within(card).getByRole("button", { name: "Save my decision" }), h);
  } finally { h.release(); }
});

test("choosing Relevant disables the choices while it saves", async () => {
  inCase("launch");
  serveThings();
  const h = hold();
  server.use(http.patch("/v1/cases/:caseId/findings/:id", async () => (await h.enter(), json(finding({ decision: "relevant" })))));
  const { user } = openThings();
  const card = await screen.findByRole("article", { name: CARD });
  const choice = within(card).getByRole("button", { name: "Relevant" });
  try {
    await user.click(choice);
    await waitFor(() => expect(choice).toBeDisabled());
    expect(card).toHaveAttribute("aria-busy", "true");
    await user.click(within(card).getByRole("button", { name: "Not relevant" }));
    expect(h.count()).toBe(1);
  } finally { h.release(); }
});

test("checking again goes busy and sends once", async () => {
  inCase("launch");
  serveThings();
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/findings/rerun", async () => (await h.enter(), json({ job_id: "e1111111-1111-4111-8111-111111111111" }, 202))));
  const { user } = openThings();
  try {
    await expectBusyOnce(user, await byName("Check again"), h);
  } finally { h.release(); }
});

test("saving the days goes busy and sends once", async () => {
  inCase("launch");
  serveThings();
  const h = hold();
  server.use(http.patch("/v1/me/checks", async () => (await h.enter(), json({ check_key: "abnormal_result_no_action", enabled: true, window_days: 21 }))));
  const { user } = openThings();
  await user.click(await screen.findByText("Change the days allowed"));
  const box = await screen.findByRole("spinbutton");
  await user.clear(box);
  await user.type(box, "21");
  try {
    await expectBusyOnce(user, await byName("Save days"), h);
  } finally { h.release(); }
});

test("turning a check off disables its switch while it saves", async () => {
  inCase("launch");
  serveThings();
  const h = hold();
  server.use(http.patch("/v1/me/checks", async () => (await h.enter(), json({ check_key: "abnormal_result_no_action", enabled: false, window_days: 14 }))));
  const { user } = openThings();
  const toggle = await screen.findByRole("switch");
  try {
    await user.click(toggle);
    await waitFor(() => expect(toggle).toBeDisabled());
    await user.click(toggle);
    expect(h.count()).toBe(1);
  } finally { h.release(); }
});

test("adding your own check goes busy and sends once", async () => {
  inCase("launch");
  serveThings();
  const h = hold();
  server.use(
    http.post("/v1/me/checks/custom", async () => (await h.enter(), json({ key: "own_1", rule: "not_followed_up", gentle: false, default_on: true, enabled: true, window_days: 30, label: "L", custom: true }, 201))),
  );
  const { user } = openThings();
  await user.click(await byName("Add your own check"));
  const dialog = await screen.findByRole("dialog", { name: "Add your own check" });
  try {
    await user.type(within(dialog).getByLabelText("What should we look for?"), "Letters answered");
    const from = within(dialog).getByLabelText("Starting from");
    await waitFor(() => expect(within(from).getAllByRole("option").length).toBeGreaterThan(2));
    const kinds = within(from).getAllByRole("option").map((o) => (o as HTMLOptionElement).value).filter(Boolean);
    await user.selectOptions(from, kinds[0]!);
    await user.selectOptions(within(dialog).getByLabelText("A follow-up of"), kinds[1]!);
    await expectBusyOnce(user, within(dialog).getByRole("button", { name: "Add check" }), h);
  } finally { h.release(); }
});

async function openLinks() {
  const { user } = openThings();
  await user.click(await screen.findByText("Look at the links to the questions"));
  return { user, links: await screen.findByRole("region", { name: "Links to the questions" }) };
}

test("confirming a link goes busy and sends once", async () => {
  inCase("launch");
  serveThings();
  const h = hold();
  server.use(http.patch("/v1/cases/:caseId/fact-links/:id", async () => (await h.enter(), json(link({ status: "confirmed", version: 2 })))));
  const { user, links } = await openLinks();
  try {
    await expectBusyOnce(user, await within(links).findByRole("button", { name: "Confirm link" }), h);
  } finally { h.release(); }
});

test("asking for link suggestions goes busy and sends once", async () => {
  inCase("launch");
  serveThings();
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/fact-links/suggest", async () => (await h.enter(), json({ job_id: "e1111111-1111-4111-8111-111111111111" }, 202))));
  const { user, links } = await openLinks();
  try {
    await expectBusyOnce(user, await within(links).findByRole("button", { name: "Suggest links" }), h);
  } finally { h.release(); }
});

test("adding a link goes busy and sends once", async () => {
  inCase("launch");
  serveThings();
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/fact-links", async () => (await h.enter(), json(link({ source: "expert", status: "confirmed", question_id: Q_2 }), 201))));
  const { user, links } = await openLinks();
  try {
    const entryBox = await within(links).findByLabelText("Entry");
    await waitFor(() => expect(within(entryBox).getAllByRole("option").length).toBeGreaterThan(1));
    const questionBox = within(links).getByLabelText("Question");
    await user.selectOptions(entryBox, (within(entryBox).getAllByRole("option")[1] as HTMLOptionElement).value);
    await user.selectOptions(questionBox, (within(questionBox).getAllByRole("option")[1] as HTMLOptionElement).value);
    await expectBusyOnce(user, within(links).getByRole("button", { name: "Add link" }), h);
  } finally { h.release(); }
});
