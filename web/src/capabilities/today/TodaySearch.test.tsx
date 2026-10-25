// S03 Cases: searching and filtering happen on the server (rule 4). The page sends `q` and
// `organisation_id` to /v1/today, keeps them in the address, and shows the counts the server sends.
import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";
import { http, json, server, signedIn } from "../../test/api";
import { CASE_A, CASE_B, caseCard, PERSONAL_ORG, TEAM_ORG, today } from "../../test/fixtures";
import { renderAt } from "../../test/render";

const everything = today({
  cases: [
    caseCard({ case_id: CASE_A, label: "Mrs A" }),
    caseCard({ case_id: CASE_B, label: "Ms C", organisation_id: TEAM_ORG, organisation_name: "Northgate", organisation_kind: "team" }),
  ],
  organisations: [
    { id: PERSONAL_ORG, name: "Jane Morgan", kind: "personal", open_count: 7 },
    { id: TEAM_ORG, name: "Northgate", kind: "team", open_count: 9 },
  ],
  open_count: 16,
  closed_count: 3,
});

let asked: URLSearchParams[] = [];

beforeEach(() => {
  asked = [];
  signedIn({ today: everything });
  // The server does the narrowing; the browser must not.
  server.use(
    http.get("/v1/today", ({ request }) => {
      const query = new URL(request.url).searchParams;
      asked.push(query);
      const q = query.get("q");
      if (q === "zz") return json({ ...everything, cases: [caseCard({ case_id: CASE_B, label: "Zz found" })], open_count: 1 });
      if (query.get("organisation_id") === PERSONAL_ORG) return json({ ...everything, cases: [caseCard({ case_id: CASE_A, label: "Mrs A" })], open_count: 5 });
      return json(everything);
    }),
  );
});

test("the chips and the heading show the server's counts, not a count of what was fetched", async () => {
  renderAt("/cases");
  const filter = await screen.findByRole("group", { name: "Show cases for" });
  expect(within(filter).getByRole("button", { name: "All · 16" })).toBeInTheDocument();
  expect(within(filter).getByRole("button", { name: "My practice · 7" })).toBeInTheDocument();
  expect(within(filter).getByRole("button", { name: "Northgate · 9" })).toBeInTheDocument();
  expect(screen.getByText("16 open · 3 closed")).toBeInTheDocument();
});

test("choosing an organisation asks the server and keeps it in the address", async () => {
  const { user, router } = renderAt("/cases");
  await user.click(await screen.findByRole("button", { name: "My practice · 7" }));
  await waitFor(() => expect(asked.some((q) => q.get("organisation_id") === PERSONAL_ORG)).toBe(true));
  expect(await screen.findByText("5 open · 3 closed")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /Ms C/ })).toBeNull();
  expect(router.state.location.search).toContain(`organisation_id=${PERSONAL_ORG}`);
});

test("typing in the search box sends q to the server after a short pause", async () => {
  const { user, router } = renderAt("/cases");
  await user.type(await screen.findByRole("searchbox", { name: "Find a case by name" }), "zz");
  await waitFor(() => expect(asked.some((q) => q.get("q") === "zz")).toBe(true));
  expect(asked.filter((q) => q.get("q") === "z")).toHaveLength(0); // debounced: no request per key
  expect(await screen.findByRole("link", { name: /Zz found/ })).toBeInTheDocument();
  expect(screen.getByText("1 open · 3 closed")).toBeInTheDocument();
  expect(router.state.location.search).toContain("q=zz");
});

test("the address restores the search and the organisation", async () => {
  renderAt(`/cases?q=zz&organisation_id=${TEAM_ORG}`);
  await waitFor(() => expect(asked.some((q) => q.get("q") === "zz" && q.get("organisation_id") === TEAM_ORG)).toBe(true));
  expect(await screen.findByRole("searchbox", { name: "Find a case by name" })).toHaveValue("zz");
});
