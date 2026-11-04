// S04 "Who is this about?": the person's name, other names and date of birth are sent to the server to be
// sealed (rule 7); a banner asks for them until they are recorded. The values are never shown back.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, HttpResponse, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { caseItem, inCase } from "./testing";

const BANNER =
  "Add the person's name and date of birth. We use them to hide their details and to stop the date of birth being read as the date of a visit.";
const DOB_BANNER =
  "Add the person's date of birth. We use it to stop the date of birth being read as the date of a visit.";
const DOB_HINT =
  "Please add this. Records print the date of birth on most pages. We use it to tell it apart from the dates of visits and results, so the transcript and timeline show the right dates.";

function mockCase(recorded: boolean, sent: unknown[] = [], dobRecorded = recorded) {
  inCase("launch");
  server.use(
    http.get("/v1/cases/:caseId", () => HttpResponse.json(caseItem("launch", { due_date: null }), { headers: { etag: '"3"' } })),
    http.get("/v1/cases/:caseId/instructions", () => json([])),
    http.get("/v1/cases/:caseId/parties", () => json([])),
    http.get("/v1/cases/:caseId/instructing-parties", () => json({ current: [], history: [], joint: false })),
    http.get("/v1/cases/:caseId/prefill", () => json({ reading: false, rejected: 0, suggestions: [] })),
    http.get("/v1/cases/:caseId/subject", () =>
      json({ status: null, label: "person", representative: null, identity_recorded: recorded, date_of_birth_recorded: dobRecorded }),
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
  expect(await within(region).findByText("Their name and date of birth are saved.")).toBeInTheDocument();
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

test("the date of birth field says why it matters, and the intro explains the same", async () => {
  mockCase(false);
  renderAt(`/cases/${CASE_A}/details`);
  const region = await screen.findByRole("region", { name: "Who is this about?" });
  expect(within(region).getByText(DOB_HINT)).toBeInTheDocument();
  expect(within(region).getByLabelText("Date of birth")).toHaveAccessibleDescription(DOB_HINT);
  expect(
    within(region).getByText(
      "We use this to hide their details from the reading tools and to tell their date of birth apart from the dates in the records. It is never shown again.",
    ),
  ).toBeInTheDocument();
  expect(within(region).getByLabelText("Date of birth")).not.toBeRequired();
});

test("recorded without a date of birth: the banner asks for the date and the top line says only the name is saved", async () => {
  mockCase(true, [], false);
  renderAt(`/cases/${CASE_A}/details`);
  expect(await screen.findByText(DOB_BANNER)).toBeInTheDocument();
  expect(screen.queryByText(BANNER)).not.toBeInTheDocument();
  const region = await screen.findByRole("region", { name: "Who is this about?" });
  expect(within(region).getByText("Their name is saved.")).toBeInTheDocument();
});

test("recorded with a date of birth: no banner and the top line says both are saved", async () => {
  mockCase(true, [], true);
  renderAt(`/cases/${CASE_A}/details`);
  const region = await screen.findByRole("region", { name: "Who is this about?" });
  expect(await within(region).findByText("Their name and date of birth are saved.")).toBeInTheDocument();
  expect(screen.queryByText(DOB_BANNER)).not.toBeInTheDocument();
});

test("saving with a date of birth shows a clear confirmation under the button until the next edit", async () => {
  mockCase(false);
  const { user } = renderAt(`/cases/${CASE_A}/details`);
  const region = await screen.findByRole("region", { name: "Who is this about?" });
  await user.type(within(region).getByLabelText("Their name"), "Zelda Quuxmore");
  await user.type(within(region).getByLabelText("Date of birth"), "03/04/1960");
  await user.click(within(region).getByRole("button", { name: "Save" }));
  const done = await within(region).findByText("Saved. Their name and date of birth are saved.");
  expect(done.closest("[role=status]")).not.toBeNull();
  expect(screen.queryByText(DOB_BANNER)).not.toBeInTheDocument();
  await user.type(within(region).getByLabelText("Their name"), "Z");
  expect(within(region).queryByText("Saved. Their name and date of birth are saved.")).not.toBeInTheDocument();
});

test("saving without a date of birth says it is not saved and asks for it", async () => {
  mockCase(false);
  const { user } = renderAt(`/cases/${CASE_A}/details`);
  const region = await screen.findByRole("region", { name: "Who is this about?" });
  await user.type(within(region).getByLabelText("Their name"), "Zelda Quuxmore");
  await user.click(within(region).getByRole("button", { name: "Save" }));
  expect(
    await within(region).findByText("Saved. Their name is saved. Their date of birth is not \u2014 please add it."),
  ).toBeInTheDocument();
  expect(await screen.findByText(DOB_BANNER)).toBeInTheDocument();
  expect(screen.queryByText(BANNER)).not.toBeInTheDocument();
});
