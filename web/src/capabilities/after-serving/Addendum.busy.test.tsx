// S21b: every action shows it is working and ignores a second click while it runs.
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { AFTER_MESSAGES as M } from "./words";
import { byName, expectBusyOnce, hold } from "./busyTesting";
import { change, P1, serve, V2 } from "./testing";

const url = `/cases/${CASE_A}/correct-report`;

test("building the supplementary report goes busy and sends once", async () => {
  serve("launch", { changes: [change()] });
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/report-changes/supplementary", async () => (await h.enter(), json({ job_id: "j" }, 202))));
  const { user } = renderAt(url);
  try {
    await expectBusyOnce(user, await byName(M["after_serving.change.build"]!), h);
  } finally { h.release(); }
});

test("adding a change goes busy and sends once", async () => {
  serve("launch");
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/report-changes", async () => (await h.enter(), json({ id: "c", job_id: "j" }, 201))));
  const { user } = renderAt(url);
  try {
    await user.selectOptions(await screen.findByLabelText(M["after_serving.change.paragraph"]!), P1);
    await user.click(screen.getByRole("radio", { name: new RegExp(M["after_serving.change.kind.change_of_opinion"]!) }));
    await user.type(screen.getByLabelText(M["after_serving.change.now"]!), "Referred by 15 May.");
    await user.type(screen.getByLabelText(M["after_serving.change.reason"]!), "Having considered their report.");
    await expectBusyOnce(user, await byName(M["after_serving.change.add"]!), h);
  } finally { h.release(); }
});

test("signing goes busy and sends once", async () => {
  serve("launch", { changes: [change()] });
  const h = hold();
  server.use(
    http.get("/v1/cases/:caseId/report-changes", () => json({ changes: [change()], supplementary: { id: V2, display_name: "Supplementary 1", status: "draft", version_no: 2, locked_at: null, prev_version_id: "v1", sha256: null } } as never)),
    http.post("/v1/cases/:caseId/report/versions/:v/sign", async () => (await h.enter(), json({ version: { id: V2, display_name: "S", status: "locked", type: "supplementary", version_no: 2, locked_at: "2027-02-20T10:00:00Z", prev_version_id: "v1", sha256: "a" } }))),
  );
  const { user } = renderAt(url);
  try {
    await user.click(await screen.findByRole("checkbox", { name: M["after_serving.change.confirm"]! }));
    await expectBusyOnce(user, await byName(M["after_serving.change.sign"]!), h);
  } finally { h.release(); }
});
