// S18 Sign and send: the declaration from the pack, the confirm box, signing and serving behind the code (step-up),
// downloads, and a served report shown locked and read-only with its servings.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { launchWords } from "../setup/testing";
import { declarations, failedCheck, finalCheck, reportOut, serve, V1, versionOut, type PackRun } from "./testing";
import { REPORT_MESSAGES as M } from "./words";

const url = `/cases/${CASE_A}/sign`;
const SUBMIT = /^Sign and send to the /;
const CONFIRM = /^I have read the whole report and I confirm the /;
const withStatus = (run: PackRun, status: string) => reportOut(run, { version: versionOut({ status }), versions: [versionOut({ status })] });

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("shows the declaration wording from the pack, the downloads, and passes axe", async () => {
    serve(run, { check: finalCheck() });
    const { container } = renderAt(url);
    expect(await screen.findByRole("heading", { level: 1, name: M["report.sign.title_ready"] })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { level: 1, name: M["report.sign.title_ready"]! })).toBeInTheDocument();
    for (const d of declarations(run)) for (const part of d.text.split("\n\n")) expect(screen.getByText(part)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: M["report.sign.download_word"]! })).toHaveAttribute("href", expect.stringContaining(`/v1/cases/${CASE_A}/report/versions/${V1}/download?format=docx`));
    expect(screen.getByRole("link", { name: M["report.sign.download_pdf"]! })).toHaveAttribute("href", expect.stringContaining("format=pdf"));
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("with no report yet, there is nothing to sign", async () => {
  serve("launch", { report: null });
  renderAt(url);
  expect(await screen.findByText(M["report.sign.nothing"]!)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: SUBMIT })).toBeNull();
});

test("if the final check has not passed, it says so and points to the check", async () => {
  serve("launch", { check: failedCheck() });
  renderAt(url);
  expect(await screen.findByText(M["report.sign.not_ready"]!)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: M["report.sign.go_check"]! })).toHaveAttribute("href", `/cases/${CASE_A}/final-check`);
});

test("signing needs the confirm box ticked, asks for the code, and then shows the locked report", async () => {
  let signed = false;
  let stepped = false;
  const bodies: unknown[] = [];
  serve("launch", { check: finalCheck() });
  server.use(
    http.get("/v1/cases/:caseId/report", () => json(withStatus("launch", signed ? "locked" : "draft"))),
    http.post("/v1/cases/:caseId/report/sign", async ({ request }) => {
      if (!stepped) return apiError(403, "step_up_required");
      bodies.push(await request.json());
      signed = true;
      return json({ version: { id: V1, version_no: 1, display_name: "Report 1", status: "locked", locked_at: "2026-10-18T11:00:00Z", sha256: "a", docx_sha256: "b", pdf_sha256: "c" } });
    }),
    http.post("/v1/auth/mfa/verify", () => ((stepped = true), json({ status: "signed_in", recovery_codes: null }))),
  );
  const { user } = renderAt(url);
  const button = await screen.findByRole("button", { name: SUBMIT });
  expect(button).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: CONFIRM }));
  await user.click(button);
  const dialog = await screen.findByRole("dialog", { name: "Please enter your code" });
  await user.type(within(dialog).getByLabelText("6-digit code"), "123456");
  await user.click(within(dialog).getByRole("button", { name: "Carry on" }));
  expect(await screen.findByText(M["report.sign.signed"]!)).toBeInTheDocument();
  expect(bodies).toEqual([{ declaration_confirmed: true }]);
  expect(screen.queryByRole("button", { name: SUBMIT })).toBeNull();
  expect(screen.getByRole("button", { name: M["report.sign.serve"]! })).toBeInTheDocument();
});

test.each([["final_check_needed"], ["final_check_failed"], ["citation_problems"], ["signature_unavailable"]])(
  "a refused signing (%s) is explained in plain words",
  async (code) => {
    serve("launch", { check: finalCheck() });
    server.use(http.post("/v1/cases/:caseId/report/sign", () => apiError(409, code)));
    const { user } = renderAt(url);
    await user.click(await screen.findByRole("checkbox", { name: CONFIRM }));
    await user.click(screen.getByRole("button", { name: SUBMIT }));
    expect(await screen.findByText(M[`report.sign.error.${code}`]!)).toBeInTheDocument();
  },
);

test("changed declaration wording must be read first; reading it lets you sign", async () => {
  serve("launch", { check: finalCheck() });
  let acknowledged = false;
  server.use(
    http.post("/v1/cases/:caseId/report/sign", () => apiError(409, "declaration_not_acknowledged")),
    http.post("/v1/cases/:caseId/report/declarations/acknowledge", () => ((acknowledged = true), json({ acknowledged: "1" }))),
  );
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("checkbox", { name: CONFIRM }));
  await user.click(screen.getByRole("button", { name: SUBMIT }));
  await user.click(await screen.findByRole("button", { name: M["report.sign.acknowledge"]! }));
  expect(await screen.findByText(M["report.sign.acknowledged"]!)).toBeInTheDocument();
  expect(acknowledged).toBe(true);
});

test("a signed report can be marked as sent with the date and how", async () => {
  const { sent, record } = serve("launch", { report: withStatus("launch", "locked"), check: finalCheck() });
  server.use(http.post("/v1/cases/:caseId/report/versions/:v/serve", async ({ request }) => (await record(request), json({ id: V1, status: "served" }))));
  const { user } = renderAt(url);
  const date = await screen.findByLabelText(M["report.sign.served_on"]!);
  await user.clear(date);
  await user.type(date, "2026-11-28");
  await user.selectOptions(screen.getByLabelText(M["report.sign.sent_via"]!), "post");
  await user.click(screen.getByRole("button", { name: M["report.sign.serve"]! }));
  await waitFor(() => expect(sent).toContainEqual({ method: "POST", path: `/v1/cases/${CASE_A}/report/versions/${V1}/serve`, body: { served_on: "2026-11-28", sent_via: "post", recipient_party_id: null }, ifMatch: null }));
});

test("a served report is read-only: status Served, no sign or serve, its servings, and the way on to the invoice", async () => {
  serve("launch", {
    report: withStatus("launch", "served"),
    check: finalCheck(),
    servings: [{ id: "s1", served_on: "2026-11-28", sent_via: "email", created_at: "2026-11-28T10:00:00Z", recipient_party_id: null, correction_of: null, reason: null }],
  });
  renderAt(url);
  expect(await screen.findByText(M["report.sign.served"]!)).toBeInTheDocument();
  expect(screen.getByText(M["report.sign.served_note"]!)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: SUBMIT })).toBeNull();
  expect(screen.queryByRole("button", { name: M["report.sign.serve"]! })).toBeNull();
  expect(await screen.findByText(/November 2026 · Email/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: M["report.sign.invoice"]! })).toHaveAttribute("href", `/cases/${CASE_A}/invoice`);
  expect(screen.getByRole("link", { name: M["report.sign.download_pdf"]! })).toBeInTheDocument();
});

test("withdrawing a served report asks for a reason first", async () => {
  const { sent, record } = serve("launch", { report: withStatus("launch", "served"), check: finalCheck() });
  server.use(http.post("/v1/cases/:caseId/report/versions/:v/withdraw", async ({ request }) => (await record(request), json({ id: V1, status: "withdrawn" }))));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["report.sign.withdraw"]! }));
  const dialog = await screen.findByRole("dialog", { name: M["report.sign.withdraw_title"]! });
  await user.click(within(dialog).getByRole("button", { name: M["report.sign.withdraw_confirm"]! }));
  expect(within(dialog).getByText(M["report.sign.reason_needed"]!)).toBeInTheDocument();
  await user.type(within(dialog).getByLabelText(M["report.sign.withdraw_reason"]!), "Instructions ended");
  await user.click(within(dialog).getByRole("button", { name: M["report.sign.withdraw_confirm"]! }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "POST", body: { reason: "Instructions ended" } }));
});

test("a ready report says so in the title with how many checks passed, and shows the document and signature cards", async () => {
  serve("launch", { check: finalCheck() });
  renderAt(url);
  expect(await screen.findByRole("heading", { level: 1, name: M["report.sign.title_ready"]! })).toBeInTheDocument();
  expect(screen.getByText("All 2 checks passed.")).toBeInTheDocument();
  const doc = screen.getByRole("region", { name: "Draft 1" });
  expect(within(doc).getByText(/^31 pages/)).toBeInTheDocument();
  expect(within(doc).getByText("Version dated 18 October 2026")).toBeInTheDocument();
  const signature = screen.getByRole("region", { name: M["report.sign.signature"]! });
  expect(await within(signature).findByText("Jane Morgan, Doctor · Body 123")).toBeInTheDocument();
});

test("the lock note says the version is locked once sent", async () => {
  serve("launch", { check: finalCheck() });
  renderAt(url);
  expect(await screen.findByText(/^Once sent, this version is locked, along with the CV, declaration and signature in it/)).toBeInTheDocument();
});

test("the signature line leaves out what the profile does not have", async () => {
  serve("launch", { check: finalCheck() });
  server.use(http.get("/v1/me/profile", () => json({ full_name: "Jane Morgan", profession: null, registration_body: null, registration_number: null, specialties: [] } as never)));
  renderAt(url);
  const signature = await screen.findByRole("region", { name: M["report.sign.signature"]! });
  expect(await within(signature).findByText("Jane Morgan")).toBeInTheDocument();
});
