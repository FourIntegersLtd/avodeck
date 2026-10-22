// What the S11, S12 and S13 boards show that the server now sends: lane icon and subtitle, the "Question N only"
// chip, the author line, the pack's card titles, the entry highlighted and later entries blurred on the page, and
// finding titles and icons from the pack with decided findings folded into one-line rows.
import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { http, HttpResponse, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { forgetPages } from "./pageImage";
import { consultation, event, FACT_1, FACT_2, FACT_3, finding, inCase, launchWords, Q_2, timeline } from "./testing";

function serveTimeline(body = timeline()) {
  const seen: URL[] = [];
  server.use(
    http.get("/v1/cases/:caseId/timeline", ({ request }) => {
      seen.push(new URL(request.url));
      return json(body);
    }),
  );
  return seen;
}

describe("S11 lanes", () => {
  test("a lane shows the pack's icon and its subtitle instead of a bare count", async () => {
    inCase("launch");
    serveTimeline(
      timeline({
        lanes: [
          { key: "lane_one", label: "Lane one words", events: [event(), event({ fact_id: FACT_3 })], colour: "#2a6f97", icon: "flask", subtitle: "2 zz tests" },
          { key: "lane_two", label: "Lane two words", events: [event({ fact_id: FACT_2 })], colour: null, icon: null, subtitle: null },
        ],
      }),
    );
    renderAt(`/cases/${CASE_A}/timeline`);
    const first = await screen.findByRole("region", { name: "Lane one words" });
    expect(within(first).getByText("2 zz tests")).toBeInTheDocument();
    expect(within(first).queryByText("2 entries in this period")).not.toBeInTheDocument();
    expect(within(first).getByTestId("lane-icon").querySelector("svg")).not.toBeNull();
    const second = screen.getByRole("region", { name: "Lane two words" });
    expect(within(second).getByText("1 entry in this period")).toBeInTheDocument();
    expect(within(second).getByTestId("lane-icon")).toHaveTextContent("L");
  });

  test("an icon name this screen has no drawing for falls back to the first letter", async () => {
    inCase("fixture");
    serveTimeline(timeline({ lanes: [{ key: "lane_one", label: "Zed words", events: [event()], icon: "no_such_drawing", subtitle: "1 zz" }] }));
    renderAt(`/cases/${CASE_A}/timeline`);
    const lane = await screen.findByRole("region", { name: "Zed words" });
    expect(within(lane).getByTestId("lane-icon")).toHaveTextContent("Z");
  });

  test("choosing a question shows a 'Question N only' chip, and clearing it asks for every question", async () => {
    inCase("launch");
    const seen = serveTimeline();
    const { user } = renderAt(`/cases/${CASE_A}/timeline`);
    const pills = await screen.findByRole("radiogroup", { name: "Linked to" });
    expect(screen.queryByText("Question 2 only")).not.toBeInTheDocument();
    await user.click(await within(pills).findByRole("radio", { name: "Q2" }));
    expect(await screen.findByText("Question 2 only")).toBeInTheDocument();
    await waitFor(() => expect(seen.at(-1)!.searchParams.get("question_id")).toBe(Q_2));
    await user.click(screen.getByRole("button", { name: "Show every question" }));
    await waitFor(() => expect(seen.at(-1)!.searchParams.get("question_id")).toBeNull());
    expect(screen.queryByText("Question 2 only")).not.toBeInTheDocument();
  });
});

describe("S12 card titles, author line and the page", () => {
  function serve(body = consultation()) {
    server.use(
      http.get("/v1/cases/:caseId/consultations/:factId", () => json(body)),
      http.get("/v1/cases/:caseId/consultations/:factId/later-notes", () => json({ notes: [] })),
      http.get("/v1/cases/:caseId/pages/:pageNo/image", () => new HttpResponse(new Uint8Array([137, 80, 78, 71]), { headers: { "content-type": "image/png" } })),
      http.get("/v1/cases/:caseId/comments", () => json({ comments: [] })),
    );
  }
  const open = () => renderAt(`/cases/${CASE_A}/consultation/${FACT_1}`);
  beforeEach(() => {
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:page-118"), revokeObjectURL: vi.fn() }));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    forgetPages();
  });

  test("shows the author line under the heading and the pack's titles on the two cards", async () => {
    inCase("launch");
    const base = consultation();
    serve({ ...base, entry: { ...base.entry, author_line: "Dr Q · zz mode" }, seen_title: "What the zz had in front of them", unseen_title: "Not in front of the zz" });
    open();
    expect(await screen.findByText("Dr Q · zz mode")).toBeInTheDocument();
    const seen = screen.getByRole("region", { name: "What the zz had in front of them" });
    expect(within(seen).getByText("p.117")).toBeInTheDocument();
    const unknown = screen.getByRole("region", { name: "Not in front of the zz" });
    expect(within(unknown).getByText("p.150")).toBeInTheDocument();
  });

  test("keeps the plain titles when the pack gives none, and no clinician word shows under the fixture packs", async () => {
    inCase("fixture");
    serve();
    open();
    expect(await screen.findByRole("region", { name: "What was in the record that day" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Not known when this arrived" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(launchWords());
  });

  test("highlights the entry and blurs the later entries on the page, from the server's boxes", async () => {
    forgetPages();
    inCase("launch");
    serve({
      ...consultation(),
      page_marks: [
        { fact_id: FACT_1, role: "entry", box: { x0: 0.1, y0: 0.4, x1: 0.9, y1: 0.5 } },
        { fact_id: FACT_3, role: "later", box: { x0: 0.1, y0: 0.6, x1: 0.9, y1: 0.7 } },
      ],
    });
    const { container } = open();
    const page = await screen.findByRole("region", { name: "This visit's page" });
    await within(page).findByRole("img");
    const mark = within(page).getByTestId("page-mark-entry");
    expect(mark).toHaveStyle({ left: "10%", top: "40%", width: "80%", height: "10%" });
    const blur = within(page).getByTestId("page-mark-later");
    expect(blur).toHaveStyle({ left: "10%", top: "60%", width: "80%", height: "10%" });
    expect(within(page).getByText("Later entries are blurred")).toBeInTheDocument();
    expect(await axeViolations(container)).toEqual([]);
  });

  test("shows no marks when the server has none", async () => {
    forgetPages();
    inCase("launch");
    serve();
    open();
    const page = await screen.findByRole("region", { name: "This visit's page" });
    await within(page).findByRole("img");
    expect(within(page).queryByTestId("page-mark-entry")).not.toBeInTheDocument();
    expect(within(page).queryByTestId("page-mark-later")).not.toBeInTheDocument();
    expect(within(page).queryByText("Later entries are blurred")).not.toBeInTheDocument();
  });
});

describe("S13 titles, icons and decided findings", () => {
  const second = finding({ id: "c2222222-2222-4222-8222-222222222222", check_key: "referral_acknowledged", title: "Zz referral title", icon: "mail", decision: "not_relevant", reason: "Seen elsewhere", evidence: [{ fact_id: FACT_3, kind: "referral", page_no: 1071, event_date: "2024-06-03", summary: "Referral" }], evidence_pages: [1071] });
  const first = finding({ title: "Zz result title", icon: "flask" });
  function serve(list = [first, second]) {
    server.use(
      http.get("/v1/cases/:caseId/findings", () => json({ findings: list })),
      http.get("/v1/cases/:caseId/fact-links", () => json({ links: [] })),
      http.get("/v1/me/checks", () =>
        json({
          checks: [
            { key: "abnormal_result_no_action", rule: "no_action_within", gentle: false, default_on: true, enabled: true, window_days: 14, title: "Zz result title", icon: "flask" },
            { key: "referral_acknowledged", rule: "not_acknowledged_within", gentle: false, default_on: true, enabled: true, window_days: 42, title: "Zz referral title", icon: "mail" },
          ],
        }),
      ),
    );
  }
  const open = () => renderAt(`/cases/${CASE_A}/things-to-look-at`);

  test("an open finding is titled with the pack's words and carries the pack's icon", async () => {
    inCase("launch");
    serve();
    open();
    const card = await screen.findByRole("article", { name: "Zz result title" });
    expect(within(card).getByTestId("finding-icon").querySelector("svg")).not.toBeNull();
    expect(screen.getByText("1 of 2 decided")).toBeInTheDocument();
  });

  test("a decided finding is folded into one line with its title, date and decision, and opens to change it", async () => {
    inCase("launch");
    serve();
    const { user } = open();
    await screen.findByRole("article", { name: "Zz result title" });
    const folded = screen.getByRole("group", { name: "Zz referral title" });
    expect(within(folded).getByText("Zz referral title · 3 Jun")).toBeInTheDocument();
    expect(within(folded).getByText("Not relevant")).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "Zz referral title" })).not.toBeInTheDocument();
    await user.click(within(folded).getByText("Zz referral title · 3 Jun"));
    const card = await screen.findByRole("article", { name: "Zz referral title" });
    expect(within(card).getByRole("radio", { name: "Relevant" })).toBeInTheDocument();
  });

  test("the checks list uses the pack's titles", async () => {
    inCase("fixture");
    serve();
    open();
    const checks = await screen.findByRole("region", { name: "The checks we run" });
    expect(await within(checks).findByRole("switch", { name: "Zz result title" })).toBeInTheDocument();
    expect(await within(checks).findByRole("switch", { name: "Zz referral title" })).toBeInTheDocument();
  });

  test("deciding an open finding folds it away", async () => {
    inCase("launch");
    serve();
    server.use(http.patch("/v1/cases/:caseId/findings/:id", () => json({ ...first, decision: "relevant", reason: null, version: 2 })));
    const { user } = open();
    const card = await screen.findByRole("article", { name: "Zz result title" });
    await user.click(within(card).getByRole("radio", { name: "Relevant" }));
    expect(await screen.findByText("2 of 2 decided")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("article", { name: "Zz result title" })).not.toBeInTheDocument());
    expect(screen.getByRole("group", { name: "Zz result title" })).toBeInTheDocument();
  });
});
