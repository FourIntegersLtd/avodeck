// S23 Trial page numbers: adding the court bundle (its name from the pack), the matched, missing and to-check
// counts, the pages the report relies on that are missing, confirming a match, telling the instructing party
// and downloading the table (behind the code).
import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { http, json, server } from "../../test/api";
import { axeViolations, renderAt } from "../../test/render";
import { BUNDLE, blobReply, CASE, concordanceOut, launchWords, pdf, ROWS, rows, serveConcordance, term, type PackRun } from "./testing";
import { TRIAL_MESSAGES as M } from "./words";

const url = `/cases/${CASE}/trial-pages`;
const k = (key: string) => M[`trial.${key}`]!;
const fill = (text: string, params: Record<string, string | number>) => Object.entries(params).reduce((s, [p, v]) => s.replaceAll(`{{${p}}}`, String(v)), text);

beforeEach(() => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:table"), revokeObjectURL: vi.fn() }));
});
afterEach(() => vi.unstubAllGlobals());

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("with no court bundle yet, it asks for one in the pack's words and sends it", async () => {
    const { sent, record } = serveConcordance(run, concordanceOut({ bundle: null, rows: [], counts: { matched: 0, to_confirm: 0, missing: 0 } }));
    server.use(http.post("/v1/cases/:caseId/court-bundles", async ({ request }) => (await record(request), json({ job_id: "a9999999-9999-4999-8999-999999999999", court_bundle_id: BUNDLE }, 202))));
    const { user, container } = renderAt(url);
    const bundle = term(run, "court_bundle");
    expect(await screen.findByText(fill(k("pages.none"), { court_bundle: bundle }))).toBeInTheDocument();
    await user.type(screen.getByLabelText(k("pages.received_on")), "2027-06-02");
    await user.upload(screen.getByLabelText(fill(k("pages.add"), { court_bundle: bundle })), pdf("bundle.pdf"));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "POST", path: `/v1/cases/${CASE}/court-bundles`, search: "?received_on=2027-06-02", contentType: "application/pdf" }));
    expect(await axeViolations(container)).toEqual([]);
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
  });

  test("shows the counts, the missing pages and every row with both numbers", async () => {
    serveConcordance(run);
    const { container } = renderAt(url);
    const bundle = term(run, "court_bundle");
    expect(await screen.findByRole("heading", { level: 1, name: k("pages.title") })).toBeInTheDocument();
    expect(screen.getByText(fill(M["trial.pages.matched_one"]!, { count: 1 }))).toBeInTheDocument();
    const missing = screen.getByRole("region", { name: fill(M["trial.pages.missing_heading_one"]!, { count: 1, court_bundle: bundle }) });
    expect(within(missing).getByText(fill(k("pages.missing_row"), { page: 214, paras: "11, 4" }))).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByRole("columnheader", { name: fill(k("pages.col.trial"), { court_bundle: bundle }) })).toBeInTheDocument();
    const matched = within(table).getByRole("row", { name: /p\.118/ });
    expect(within(matched).getByText("2407")).toBeInTheDocument();
    expect(await axeViolations(container)).toEqual([]);
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
  });
});

test("confirming a match sends the trial page number with the row's version", async () => {
  const { sent, record } = serveConcordance("launch");
  server.use(http.patch("/v1/cases/:caseId/concordance/:rowId", async ({ request }) => (await record(request), json({ ...rows()[1]!, trial_page_no: 2391, confirmed: true, status: "matched", method: "expert", version: 3 }))));
  const { user } = renderAt(url);
  const row = await screen.findByRole("row", { name: /p\.103/ });
  const input = within(row).getByRole("spinbutton");
  await user.clear(input);
  await user.type(input, "2391");
  await user.click(within(row).getByRole("button", { name: k("pages.confirm") }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "PATCH", path: `/v1/cases/${CASE}/concordance/${ROWS[1]}`, ifMatch: '"2"', body: { trial_page_no: 2391 } }));
});

test("telling the instructing party lists the missing pages to send", async () => {
  const { sent, record } = serveConcordance("launch");
  server.use(
    http.post("/v1/cases/:caseId/concordance/tell-instructing-party", async ({ request }) => (
      await record(request), json({ missing: [{ page_no: 214, paragraphs: [11, 4] }], received_on: "2027-06-02", bundle_version: 2 })
    )),
  );
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: /^Tell / }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "POST", path: `/v1/cases/${CASE}/concordance/tell-instructing-party` }));
  expect(await screen.findByText(k("pages.told"))).toBeInTheDocument();
});

test("the table downloads as a spreadsheet behind the code", async () => {
  const { sent, record } = serveConcordance("launch");
  server.use(http.get("/v1/cases/:caseId/concordance/export", async ({ request }) => (await record(request), blobReply("text/csv"))));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: k("pages.export_csv") }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "GET", path: `/v1/cases/${CASE}/concordance/export`, search: "?format=csv" }));
  await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled());
});

test("while matching it says so", async () => {
  serveConcordance("launch", concordanceOut({ bundle: { ...concordanceOut().bundle!, status: "matching" }, rows: [], counts: { matched: 0, to_confirm: 0, missing: 0 } }));
  renderAt(url);
  expect(await screen.findByText(k("pages.matching"))).toBeInTheDocument();
});
