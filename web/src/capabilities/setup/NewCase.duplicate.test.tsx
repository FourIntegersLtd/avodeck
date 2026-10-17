// S04 "Have you had a case about this person before?": the first check compares every case you can see
// (this case's key is not saved yet); once this case's key is saved, a check again leaves this case out.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, HttpResponse, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { caseItem, inCase } from "./testing";

test("a check again, after this case's key is saved, leaves this case out", async () => {
  inCase("launch");
  const sent: unknown[] = [];
  server.use(
    http.get("/v1/cases/:caseId", () => HttpResponse.json(caseItem("launch", { due_date: null }), { headers: { etag: '"3"' } })),
    http.get("/v1/cases/:caseId/instructions", () => json([])),
    http.get("/v1/cases/:caseId/parties", () => json([])),
    http.get("/v1/cases/:caseId/instructing-parties", () => json({ current: [], history: [], joint: false })),
    http.get("/v1/cases/:caseId/prefill", () => json({ reading: false, rejected: 0, suggestions: [] })),
    http.post("/v1/cases/duplicate-check", async ({ request }) => {
      sent.push(await request.json());
      return json({ matches: [] });
    }),
    http.put("/v1/cases/:caseId/subject-key", () => new HttpResponse(null, { status: 204 })),
  );
  const { user } = renderAt(`/cases/${CASE_A}/details`);
  const same = await screen.findByRole("region", { name: "Have you had a case about this person before?" });
  await user.type(within(same).getByLabelText("Their identifier number"), "9434765919");
  await user.click(within(same).getByRole("button", { name: "Check" }));
  await waitFor(() => expect(sent).toHaveLength(1));
  await user.click(within(same).getByRole("button", { name: "Check" }));
  await waitFor(() => expect(sent).toHaveLength(2));
  expect(sent).toEqual([
    { identifier: "9434765919", jurisdiction: "gb-eaw" },
    { identifier: "9434765919", jurisdiction: "gb-eaw", case_id: CASE_A },
  ]);
});
