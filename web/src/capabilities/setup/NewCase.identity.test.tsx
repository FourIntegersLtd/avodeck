// S04 "Who is this about?": the person's name, other names and date of birth are sent to the server to be
// sealed (rule 7); a banner asks for them until they are recorded. The values are never shown back.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, HttpResponse, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { caseItem, inCase } from "./testing";

const BANNER = "Add the person's name and date of birth so we can hide them from the reading tools.";

function mockCase(recorded: boolean, sent: unknown[] = []) {
  inCase("launch");
  server.use(
    http.get("/v1/cases/:caseId", () => HttpResponse.json(caseItem("launch", { due_date: null }), { headers: { etag: '"3"' } })),
    http.get("/v1/cases/:caseId/instructions", () => json([])),
    http.get("/v1/cases/:caseId/parties", () => json([])),
    http.get("/v1/cases/:caseId/instructing-parties", () => json({ current: [], history: [], joint: false })),
    http.get("/v1/cases/:caseId/prefill", () => json({ reading: false, rejected: 0, suggestions: [] })),
    http.get("/v1/cases/:caseId/subject", () =>
      json({ status: null, label: "person", representative: null, identity_recorded: recorded }),
    ),
    http.put("/v1/cases/:caseId/subject/identity", async ({ request }) => {
      sent.push(await request.json());
      return new HttpResponse(null, { status: 204 });
    }),
  );
}

test("until the person's name is recorded a banner asks for it", async () => {
  mockCase(false);
  renderAt(`/cases/${CASE_A}/details`);
  expect(await screen.findByText(BANNER)).toBeInTheDocument();
});

test("once recorded there is no banner and the form says so without showing the values", async () => {
  mockCase(true);
  renderAt(`/cases/${CASE_A}/details`);
  const region = await screen.findByRole("region", { name: "Who is this about?" });
  expect(within(region).getByText("Their name and date of birth are saved.")).toBeInTheDocument();
  expect(screen.queryByText(BANNER)).not.toBeInTheDocument();
});

test("saving sends the name, other names and date of birth, then clears the banner", async () => {
  const sent: unknown[] = [];
  mockCase(false, sent);
  const { user } = renderAt(`/cases/${CASE_A}/details`);
  const region = await screen.findByRole("region", { name: "Who is this about?" });
  await user.type(within(region).getByLabelText("Their name"), "Zelda Quuxmore");
  await user.type(within(region).getByLabelText("Other names (optional)"), "Z Quux, Zelda Maiden");
  await user.type(within(region).getByLabelText("Date of birth"), "03/04/1960");
  await user.click(within(region).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0]).toEqual({ name: "Zelda Quuxmore", other_names: ["Z Quux", "Zelda Maiden"], date_of_birth: "03/04/1960" });
  await waitFor(() => expect(screen.queryByText(BANNER)).not.toBeInTheDocument());
  expect(within(region).getByLabelText("Their name")).toHaveValue("");
});
