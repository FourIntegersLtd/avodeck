// S03c Cases and S03e Cases (empty): every case in one searchable list.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { signedIn, http, json, server } from "../../test/api";

// The server narrows the list; this stands in for it.
function narrowing(data: ReturnType<typeof today>) {
  server.use(
    http.get("/v1/today", ({ request }) => {
      const query = new URL(request.url).searchParams;
      const q = (query.get("q") ?? "").toLowerCase();
      const org = query.get("organisation_id");
      const cases = data.cases.filter((c) => (!org || c.organisation_id === org) && c.label.toLowerCase().includes(q));
      return json({ ...data, cases, open_count: cases.length });
    }),
  );
}
import { CASE_A, CASE_B, caseCard, launchPack, PERSONAL_ORG, TEAM_ORG, today } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";

const instructingSide = launchPack.party_roles.find((r) => r.key === "instructing_side")!.label;
const total = launchPack.workflow_steps.length;
const position = launchPack.workflow_steps.findIndex((s) => s.key === "read") + 1;

const two = today({
  cases: [
    caseCard({ case_id: CASE_A, label: "Mrs A", stage: "read", due_date: "2026-11-30" }),
    caseCard({ case_id: CASE_B, label: "Ms C", organisation_id: TEAM_ORG, organisation_name: "Northgate", organisation_kind: "team", stage: "court", party_role: "instructing_side", due_date: "2026-10-21" }),
  ],
  organisations: [
    { id: PERSONAL_ORG, name: "Jane Morgan", kind: "personal", open_count: 1 },
    { id: TEAM_ORG, name: "Northgate", kind: "team", open_count: 1 },
  ],
  closed_count: 23,
});

test("/cases lists every case as a row linking to the case, with side, step and due date", async () => {
  signedIn({ today: two });
  const { container } = renderAt("/cases");
  expect(await screen.findByRole("heading", { level: 1, name: "Your cases" })).toBeInTheDocument();
  const row = await screen.findByRole("link", { name: /Ms C/ });
  expect(row).toHaveAttribute("href", `/cases/${CASE_B}/home`);
  await within(row).findByText(`For the ${instructingSide}`);
  expect(within(row).getByText("Court")).toBeInTheDocument();
  expect(within(row).getByText("21 Oct")).toBeInTheDocument();
  expect(within(row).getByText("Northgate")).toBeInTheDocument();
  expect(await within(screen.getByRole("link", { name: /Mrs A/ })).findByRole("img", { name: `Read, step ${position} of ${total}` })).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
});

test("Cases is the current place in the header, not Today", async () => {
  signedIn({ today: two });
  renderAt("/cases");
  const header = await screen.findByRole("banner");
  expect(await within(header).findByRole("link", { name: "Cases" })).toHaveAttribute("aria-current", "page");
  expect(within(header).getByRole("link", { name: "Today" })).not.toHaveAttribute("aria-current");
});

test("searching narrows the list by case name", async () => {
  signedIn({ today: two });
  narrowing(two);
  const { user } = renderAt("/cases");
  await screen.findByRole("link", { name: /Mrs A/ });
  await user.type(screen.getByRole("searchbox", { name: "Find a case by name" }), "ms c");
  await waitFor(() => expect(screen.queryByRole("link", { name: /Mrs A/ })).toBeNull());
  expect(screen.getByRole("link", { name: /Ms C/ })).toBeInTheDocument();
  await user.clear(screen.getByRole("searchbox"));
  await user.type(screen.getByRole("searchbox"), "zzz");
  expect(await screen.findByText("No case matches that.")).toBeInTheDocument();
});

test("the organisation filter narrows the rows", async () => {
  signedIn({ today: two });
  narrowing(two);
  const { user } = renderAt("/cases");
  const filter = await screen.findByRole("group", { name: "Show cases for" });
  expect(filter).toHaveClass("av-segmented-control");
  await user.click(within(filter).getByRole("button", { name: "My practice · 1" }));
  await waitFor(() => expect(screen.queryByRole("link", { name: /Ms C/ })).toBeNull());
  expect(screen.getByRole("link", { name: /Mrs A/ })).toBeInTheDocument();
  expect(screen.getByText("1 open · 23 closed")).toBeInTheDocument();
});

test("New case is the one primary button, and the footer counts the year's sides", async () => {
  const { registerScreen } = await import("../../foundation/lib/screens");
  registerScreen("new_case", () => "/cases/new");
  signedIn({ today: two });
  server.use(http.get("/v1/me/stats/balance", () => json({ counts: { other_side: 2 }, months: 12 })));
  const { container } = renderAt("/cases");
  expect(await screen.findByRole("link", { name: "New case" })).toHaveAttribute("href", "/cases/new");
  expect([...container.querySelectorAll(".av-button")].filter((el) => ![...el.classList].some((c) => /^av-button--/.test(c)))).toHaveLength(1);
  expect(await screen.findByTestId("today-balance")).toHaveTextContent("This year: 2 for the");
});

test("with no cases: No cases yet, one primary button and three info cards", async () => {
  const { registerScreen } = await import("../../foundation/lib/screens");
  registerScreen("new_case", () => "/cases/new");
  signedIn({ today: today({ cases: [], needs_you: [], closed_count: 0, organisations: [{ id: PERSONAL_ORG, name: "Jane Morgan", kind: "personal", open_count: 0 }] }) });
  const { container } = renderAt("/cases");
  expect(await screen.findByRole("heading", { level: 2, name: "No cases yet" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Start your first case" })).toHaveAttribute("href", "/cases/new");
  expect(container.querySelectorAll(".av-cases-empty__ghost")).toHaveLength(0);
  expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(3);
  expect(screen.queryByRole("searchbox")).toBeNull();
  expect([...container.querySelectorAll(".av-button")].filter((el) => ![...el.classList].some((c) => /^av-button--/.test(c)))).toHaveLength(1);
  expect(await axeViolations(container)).toEqual([]);
});

test("each row's ring is a 9px arc over a grey track, drawn by the design system's ring", async () => {
  signedIn({ today: two });
  renderAt("/cases");
  const row = await screen.findByRole("link", { name: /Mrs A/ });
  await within(row).findByRole("img", { name: /step/ });
  const circles = row.querySelectorAll(".av-ring svg circle");
  expect(circles).toHaveLength(2);
  expect(circles[0]).toHaveClass("av-ring__track");
  expect(circles[1]!.getAttribute("stroke-width")).toBe("9");
});
