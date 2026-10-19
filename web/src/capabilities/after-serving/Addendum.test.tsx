// S21b Correct or update your report: the served version read-only, a change with its reason, the paragraphs that
// rely on it, the supplementary report, and signing it behind the code. The served report is never altered.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { launchWords } from "../setup/testing";
import { AFTER_MESSAGES as M } from "./words";
import { change, declarations, P1, P2, serve, V2, type PackRun } from "./testing";

const url = `/cases/${CASE_A}/correct-report`;
const supplementary = (status: string) => ({ id: V2, display_name: "Supplementary 1", status, version_no: 2, locked_at: null, prev_version_id: "v1", sha256: null });

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("shows the served version locked and read-only, and passes axe", async () => {
    serve(run);
    const { container } = renderAt(url);
    expect(await screen.findByRole("heading", { level: 2, name: M["after_serving.change.title"] })).toBeInTheDocument();
    expect(await screen.findByText("Report 1 · locked")).toBeInTheDocument();
    expect(screen.getByText("Mrs A should have been referred in early March 2024.")).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("choosing a paragraph shows what the report said, and a change is sent with its kind, new words and reason", async () => {
  const { sent, record } = serve("launch");
  server.use(http.post("/v1/cases/:caseId/report-changes", async ({ request }) => (await record(request), json({ id: "c", job_id: "j" }, 201))));
  const { user } = renderAt(url);
  await user.selectOptions(await screen.findByLabelText(M["after_serving.change.paragraph"]!), P1);
  expect(within(screen.getByRole("region", { name: M["after_serving.change.said"] })).getByText("Mrs A should have been referred in early March 2024.")).toBeInTheDocument();
  await user.click(screen.getByRole("radio", { name: new RegExp(M["after_serving.change.kind.change_of_opinion"]!) }));
  await user.type(screen.getByLabelText(M["after_serving.change.now"]!), "Referred by 15 May at the latest.");
  const add = screen.getByRole("button", { name: M["after_serving.change.add"]! });
  expect(add).toBeDisabled();
  await user.type(screen.getByLabelText(M["after_serving.change.reason"]!), "Having considered their report.");
  await user.click(add);
  await waitFor(() => expect(sent[0]).toMatchObject({ method: "POST", body: { paragraph_id: P1, kind: "change_of_opinion", new_text: "Referred by 15 May at the latest.", reason: "Having considered their report." } }));
});

test("changes so far are listed with the paragraphs that rely on them", async () => {
  serve("launch", { changes: [change({ suggested_dependents: [{ paragraph_id: P2, depends: true, reason: "shares a citation" }, { paragraph_id: P1, depends: false, reason: "unrelated" }] })] });
  renderAt(url);
  expect(await screen.findByText("Referred by 15 May at the latest.")).toBeInTheDocument();
  expect(screen.getByText("Paragraph 73 relies on this one. Please check it still reads right.")).toBeInTheDocument();
  expect(screen.getByText("Why: shares a citation")).toBeInTheDocument();
  expect(screen.queryByText(/unrelated/)).toBeNull();
});

test("the supplementary report is drafted from the changes; with none it explains", async () => {
  const { sent, record } = serve("launch", { changes: [change()] });
  let first = true;
  server.use(http.post("/v1/cases/:caseId/report-changes/supplementary", async ({ request }) => {
    await record(request);
    if (first) {
      first = false;
      return apiError(409, "no_changes");
    }
    return json({ job_id: "j" }, 202);
  }));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: M["after_serving.change.build"]! }));
  expect(await screen.findByText(M["after_serving.change.error.no_changes"]!)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: M["after_serving.change.build"]! }));
  expect(await screen.findByText(M["after_serving.change.building"]!)).toBeInTheDocument();
  expect(sent).toHaveLength(2);
});

test("signing the supplementary report needs the box ticked and the code, then shows it locked", async () => {
  let stepped = false;
  let signed = false;
  const bodies: unknown[] = [];
  serve("launch", { changes: [change()] });
  server.use(
    http.get("/v1/cases/:caseId/report-changes", () => json({ changes: [change()], supplementary: supplementary(signed ? "locked" : "draft") } as never)),
    http.post("/v1/cases/:caseId/report/versions/:v/sign", async ({ request, params }) => {
      if (!stepped) return apiError(403, "step_up_required");
      bodies.push({ body: await request.json(), v: params.v });
      signed = true;
      return json({ version: { id: V2, display_name: "S", status: "locked", type: "supplementary", version_no: 2, locked_at: "2027-02-20T10:00:00Z", prev_version_id: "v1", sha256: "a" } });
    }),
    http.post("/v1/auth/mfa/verify", () => ((stepped = true), json({ status: "signed_in", recovery_codes: null }))),
  );
  const { user } = renderAt(url);
  for (const d of declarations("launch")) for (const part of d.text.split("\n\n")) expect(await screen.findByText(part)).toBeInTheDocument();
  const button = await screen.findByRole("button", { name: M["after_serving.change.sign"]! });
  expect(button).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: M["after_serving.change.confirm"]! }));
  await user.click(button);
  const dialog = await screen.findByRole("dialog", { name: "Please enter your code" });
  await user.type(within(dialog).getByLabelText("6-digit code"), "123456");
  await user.click(within(dialog).getByRole("button", { name: "Carry on" }));
  expect(await screen.findByText(M["after_serving.change.signed"]!)).toBeInTheDocument();
  expect(bodies).toEqual([{ body: { declaration_confirmed: true }, v: V2 }]);
  expect(screen.queryByRole("button", { name: M["after_serving.change.sign"]! })).toBeNull();
});

test("a signed supplementary report is read-only", async () => {
  serve("launch", { changes: [change()], supplementary: supplementary("locked") });
  renderAt(url);
  expect(await screen.findByText("Supplementary 1 · signed and locked")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: M["after_serving.change.sign"]! })).toBeNull();
  expect(screen.queryByRole("button", { name: M["after_serving.change.add"]! })).toBeNull();
});
