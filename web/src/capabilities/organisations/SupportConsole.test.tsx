// The support console at /support: staff only, case details only, never page content outside a grant.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { apiError, http, json, server, signedIn } from "../../test/api";
import { CASE_A, PERSONAL_ORG, me } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { setFlag } from "./flags";

// Typing into fields is slow on a busy machine; these flows have many steps.
vi.setConfig({ testTimeout: 30_000 });

afterEach(() => setFlag("support_custody_transfer", false));

const found = (over: Record<string, unknown> = {}) => ({
  case_id: CASE_A, status: "active", organisation_id: PERSONAL_ORG, organisation_kind: "personal", created_at: "2026-01-05T09:00:00Z", served: false, grant_active: false, grant_expires_at: null, ...over,
});

const list = (cases: Array<ReturnType<typeof found>>) => json({ cases, served_case_ids: cases.filter((c) => c.served).map((c) => c.case_id) });

test("someone who is not staff sees the not found page, not a console", async () => {
  signedIn();
  server.use(http.get("/v1/support/cases", () => apiError(404, "not_found")));
  const { user } = renderAt("/support");
  await user.type(await screen.findByLabelText("Case number or email"), CASE_A);
  await user.click(screen.getByRole("button", { name: "Search" }));
  expect(await screen.findByRole("heading", { level: 1 })).not.toHaveTextContent("Support");
});

test("the console explains itself at the top: search, 24-hour access, every look recorded", async () => {
  signedIn();
  renderAt("/support");
  await screen.findByRole("heading", { level: 1, name: "Support" });
  const intro = document.querySelector("h1 + p")!.textContent!;
  expect(intro).toMatch(/search every case/i);
  expect(intro).toMatch(/named expert/i);
  expect(intro).toMatch(/24 hours/);
  expect(intro).toMatch(/every look is (written down|recorded)/i);
});

test("a result card is padded inside, spaced from the search and from the next result", async () => {
  signedIn();
  server.use(http.get("/v1/support/cases", () => list([found({ grant_active: true, grant_expires_at: "2026-10-24T10:00:00Z" })])));
  const { user } = renderAt("/support");
  await user.type(await screen.findByLabelText("Case number or email"), CASE_A);
  await user.click(screen.getByRole("button", { name: "Search" }));
  const row = within(await screen.findByRole("list", { name: "Cases found" })).getByRole("listitem");
  expect(row).toHaveClass("av-org-result");
  const css = readFileSync(resolve(__dirname, "organisations.css"), "utf8");
  expect(css).toMatch(/\.av-org-result\s*\{[^}]*padding:\s*var\(--space-5\)/);
  expect(css).toMatch(/\.av-org-results\s*\{[^}]*gap:\s*var\(--space-4\)[^}]*margin-top:\s*var\(--space-4\)|\.av-org-results\s*\{[^}]*margin-top:\s*var\(--space-4\)[^}]*gap:\s*var\(--space-4\)/);
  expect(css).toMatch(/\.av-org-result[^{]*\.av-button[^{]*\{[^}]*width:\s*100%/);
});

test("staff search by case number and see details, no records", async () => {
  signedIn();
  server.use(http.get("/v1/support/cases", ({ request }) => (new URL(request.url).searchParams.get("q") === CASE_A ? list([found({ grant_active: true, grant_expires_at: "2026-10-24T10:00:00Z" })]) : list([]))));
  const { user } = renderAt("/support");
  await user.type(await screen.findByLabelText("Case number or email"), CASE_A);
  await user.click(screen.getByRole("button", { name: "Search" }));
  const row = within(await screen.findByRole("list", { name: "Cases found" })).getByRole("listitem");
  expect(within(row).getByText(/Own practice/)).toBeInTheDocument();
  expect(within(row).getByText(/Access given until/)).toBeInTheDocument();
  expect(within(row).getByText(/Not served/)).toBeInTheDocument();
});

test("the status is shown in plain words, never the raw code", async () => {
  signedIn();
  server.use(http.get("/v1/support/cases", () => list([found({ status: "served", served: true })])));
  const { user } = renderAt("/support");
  await user.type(await screen.findByLabelText("Case number or email"), CASE_A);
  await user.click(screen.getByRole("button", { name: "Search" }));
  const row = within(await screen.findByRole("list", { name: "Cases found" })).getByRole("listitem");
  expect(row).toHaveTextContent("Report sent");
  expect(row).not.toHaveTextContent("served ·");
});

test("a case with no grant offers no way to open its records", async () => {
  signedIn();
  server.use(http.get("/v1/support/cases", () => list([found()])));
  const { user } = renderAt("/support");
  await user.type(await screen.findByLabelText("Case number or email"), "a@example.test");
  await user.click(screen.getByRole("button", { name: "Search" }));
  await screen.findByRole("list", { name: "Cases found" });
  expect(screen.queryByRole("link", { name: "Open the case" })).toBeNull();
  expect(screen.getByText("You can open the records only while the expert's access is on.")).toBeInTheDocument();
});

test("the custody form shows only behind its flag", async () => {
  signedIn();
  server.use(http.get("/v1/support/cases", () => list([found({ served: true })])));
  const first = renderAt("/support");
  await first.user.type(await screen.findByLabelText("Case number or email"), CASE_A);
  await first.user.click(screen.getByRole("button", { name: "Search" }));
  await screen.findByRole("list", { name: "Cases found" });
  expect(screen.queryByRole("heading", { name: "Move custody of served cases" })).toBeNull();
  first.unmount();
  setFlag("support_custody_transfer", true);
  const second = renderAt("/support");
  await second.user.type(await screen.findByLabelText("Case number or email"), CASE_A);
  await second.user.click(screen.getByRole("button", { name: "Search" }));
  expect(await screen.findByRole("heading", { name: "Move custody of served cases" })).toBeInTheDocument();
});

test("passes the automated accessibility checks", async () => {
  signedIn();
  server.use(http.get("/v1/support/cases", () => list([found()])));
  const { container, user } = renderAt("/support");
  await user.type(await screen.findByLabelText("Case number or email"), CASE_A);
  await user.click(screen.getByRole("button", { name: "Search" }));
  await screen.findByRole("list", { name: "Cases found" });
  expect(await axeViolations(container)).toEqual([]);
});

test("staff choose which served case to move", async () => {
  setFlag("support_custody_transfer", true);
  signedIn();
const SECOND = "99999999-9999-4999-8999-999999999999";
  const sent: unknown[] = [];
  server.use(
    http.get("/v1/support/cases", () => list([found({ served: true }), found({ case_id: SECOND, served: true })])),
    // The API's one route (routes_support.py): POST /v1/support/custody-transfers with {case_id, to, evidence_note}.
    http.post("/v1/support/custody-transfers", async ({ request }) => {
      sent.push(await request.json());
      return json({ id: CASE_A, case_id: SECOND, to_person_id: null, to_organisation_id: null, transferred_at: "2026-10-24T10:00:00Z" }, 201);
    }),
  );
  const { user } = renderAt("/support");
  await user.type(await screen.findByLabelText("Case number or email"), "a@example.test");
  await user.click(screen.getByRole("button", { name: "Search" }));
  await user.selectOptions(await screen.findByLabelText("Case to move"), SECOND);
  await user.type(screen.getByLabelText("Where the evidence is kept (a reference code)"), "REF1");
  await user.click(screen.getByRole("button", { name: "Move custody" }));
  expect(await screen.findByText("Custody has moved.")).toBeInTheDocument();
  expect(sent).toEqual([{ case_id: SECOND, to: "custodian", evidence_note: "REF1" }]);
});

test("support staff signing in land on the console, with Support console in the top bar and no expert places", async () => {
  signedIn({ me: { ...me(), is_support_staff: true } });
  const { router } = renderAt("/today");
  expect(await screen.findByRole("heading", { level: 1, name: "Support" })).toBeInTheDocument();
  expect(router.state.location.pathname).toBe("/support");
  const nav = screen.getByRole("navigation");
  expect(within(nav).getByRole("link", { name: "Support console" })).toHaveAttribute("href", "/support");
  expect(within(nav).queryByRole("link", { name: "Today" })).toBeNull();
});
