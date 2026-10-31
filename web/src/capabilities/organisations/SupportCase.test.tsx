// The support console's case page at /support/cases/:caseId (support plan phase 2): where a case is stuck,
// as plain sections in counts and codes, with a plain explanation for each stuck state. Staff only.
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { apiError, http, json, server, signedIn } from "../../test/api";
import { CASE_A, PERSONAL_ORG, me } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";

vi.setConfig({ testTimeout: 30_000 });

const JOB = "11111111-1111-4111-8111-111111111111";
const PACKAGE = "22222222-2222-4222-8222-222222222222";

export const detail = (over: Record<string, unknown> = {}) => ({
  case: { case_id: CASE_A, status: "active", organisation_id: PERSONAL_ORG, organisation_kind: "personal", created_at: "2026-01-05T09:00:00Z", served: false, grant_active: false, grant_expires_at: null },
  grants: [],
  content_access: false,
  jobs: [{ id: JOB, kind: "process_pages", status: "paused", attempts: 1, error_code: "cost_guard", step: null, created_at: "2026-10-24T09:00:00Z", started_at: "2026-10-24T09:01:00Z", finished_at: null, updated_at: "2026-10-24T09:05:00Z" }],
  pages: { total: 900, unread_by_named_expert: 212, unreadable: 3, needs_clearer_copy: 2, quarantined: 1, superseded: 4 },
  facts: { suggested: 40, verified: 100, needs_recheck: 5 },
  questions: { state: "to_check", instruction_versions: [{ n: 1, read_status: "failed", job_error_code: "service_busy" }] },
  report: { latest_version_status: "draft", locked_at: null, served_at: null, final_check: { status: "failed", item_codes: ["facts_verified"] }, declaration_acknowledged: true, signature_present: true, signature_readable: true, pack_correction_pending: true },
  close: { status: "active", delete_after: null, hold: { present: true, until: "2027-01-31", reason_code: "appeal" }, deletion_started: false, export: { status: "done", downloaded: true } },
  offline_packages: [{ id: PACKAGE, state: "built", expires_at: "2026-10-27T09:00:00Z", stale_reason: "new_version", downloaded_at: null }],
  named_expert_history: [{ changed_at: "2026-10-20T09:00:00Z" }],
  deadlines: { open: 1, overdue: 2, done: 3, cancelled: 0 },
  ...over,
});

const staff = () => signedIn({ me: { ...me(), is_support_staff: true } });

test("staff see each part of the case as a plain section, with what a stuck state means", async () => {
  staff();
  server.use(http.get(`/v1/support/cases/${CASE_A}`, () => json(detail())));
  renderAt(`/support/cases/${CASE_A}`);
  expect(await screen.findByRole("heading", { level: 1, name: "Case details for support" })).toBeInTheDocument();
  for (const name of ["Work Avodeck is doing", "Pages", "Suggestions", "Questions asked", "Report", "Closing the case", "Offline copies", "Named expert changes", "Deadlines"]) {
    expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
  }
  expect(screen.getByText("Pages the expert hasn't read yet: 212. Suggestions stay hidden until a page is read; nothing is lost.")).toBeInTheDocument();
  const work = screen.getByRole("region", { name: "Work Avodeck is doing" });
  expect(within(work).getByText("Paused")).toBeInTheDocument();
  expect(within(work).getByText(/cost_guard/)).toBeInTheDocument();
  expect(within(work).getByText("Paused work waits safely; nothing is lost. It starts again when the service is back.")).toBeInTheDocument();
  expect(screen.getByText("Facts to check again after a change: 5.")).toBeInTheDocument();
  expect(screen.getByText("The court wording was corrected. The expert must read it again before signing.")).toBeInTheDocument();
  expect(screen.getByText("The letter could not be read. The expert can ask for it to be read again.")).toBeInTheDocument();
  expect(screen.getByText("A hold stops deletion until it is released.")).toBeInTheDocument();
});

test("the results list links each case to its details page", async () => {
  staff();
  server.use(
    http.get("/v1/support/cases", () => json({ cases: [detail().case], served_case_ids: [] })),
    http.get(`/v1/support/cases/${CASE_A}`, () => json(detail())),
  );
  const { user, router } = renderAt("/support");
  await user.type(await screen.findByLabelText("Case number or email"), CASE_A);
  await user.click(screen.getByRole("button", { name: "Search" }));
  await user.click(await screen.findByRole("link", { name: "See details" }));
  expect(router.state.location.pathname).toBe(`/support/cases/${CASE_A}`);
  expect(await screen.findByRole("heading", { level: 1, name: "Case details for support" })).toBeInTheDocument();
});

test("someone who is not staff sees the not found page", async () => {
  signedIn();
  server.use(http.get(`/v1/support/cases/${CASE_A}`, () => apiError(404, "not_found")));
  renderAt(`/support/cases/${CASE_A}`);
  expect(await screen.findByRole("heading", { level: 1 })).not.toHaveTextContent("support");
});

test("passes the automated accessibility checks", async () => {
  staff();
  server.use(http.get(`/v1/support/cases/${CASE_A}`, () => json(detail())));
  const { container } = renderAt(`/support/cases/${CASE_A}`);
  await screen.findByRole("heading", { level: 1, name: "Case details for support" });
  expect(await axeViolations(container)).toEqual([]);
});
