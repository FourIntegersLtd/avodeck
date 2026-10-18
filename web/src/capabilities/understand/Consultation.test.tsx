// S12 One consultation as the clinician saw it: the title is the pack's, later entries are counted and
// folded away, entries with no known arrival are shown as not known, the guidance in force is named (label and
// link only), the expert keeps a note, and changes when something arrived with a reason.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { comment, consultation, FACT_1, FACT_3, inCase, launchWords, type PackRun } from "./testing";

function serve(body = consultation()) {
  const calls: Array<{ method: string; url: URL; body: unknown }> = [];
  server.use(
    http.get("/v1/cases/:caseId/consultations/:factId", ({ request }) => {
      calls.push({ method: "GET", url: new URL(request.url), body: null });
      return json(body);
    }),
    http.put("/v1/cases/:caseId/consultations/:factId/note", async ({ request }) => {
      const sent = await request.json();
      calls.push({ method: "PUT", url: new URL(request.url), body: sent });
      return json({ fact_id: FACT_1, body: (sent as { body: string }).body });
    }),
    http.patch("/v1/cases/:caseId/facts/:factId/availability", async ({ request, params }) => {
      const sent = (await request.json()) as { available_at: string | null; unknown: boolean };
      calls.push({ method: "PATCH", url: new URL(request.url), body: sent });
      return json({ fact_id: String(params.factId), available_at: sent.available_at, unknown: sent.unknown });
    }),
    http.get("/v1/cases/:caseId/comments", () => json({ comments: [comment()] })),
    http.post("/v1/cases/:caseId/comments", async ({ request }) => {
      const sent = (await request.json()) as { body: string };
      calls.push({ method: "POST", url: new URL(request.url), body: sent });
      return json(comment({ body: sent.body }), 201);
    }),
  );
  return calls;
}
const open = () => renderAt(`/cases/${CASE_A}/consultation/${FACT_1}`);

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("is titled with the pack's words for the day, asks for the day as seen, and passes axe", async () => {
    inCase(run);
    const calls = serve(consultation({ title: run === "launch" ? "As the clinician saw it" : "What the assessor had in hand" }));
    const { container } = open();
    const title = run === "launch" ? "As the clinician saw it on 15 May 2024" : "What the assessor had in hand on 15 May 2024";
    expect(await screen.findByRole("heading", { level: 2, name: title })).toBeInTheDocument();
    expect(calls[0]!.url.searchParams.get("as_seen")).toBe("true");
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("shows what was in the record, folds what came later with its count, and lists what is not known", async () => {
  inCase("launch");
  serve();
  const { user } = open();
  const seen = await screen.findByRole("region", { name: "In the record that day" });
  expect(within(seen).getByText("p.117")).toBeInTheDocument();
  const later = screen.getByText("Show what came later (1)").closest("details")!;
  expect(later).not.toHaveAttribute("open");
  await user.click(screen.getByText("Show what came later (1)"));
  expect(later).toHaveAttribute("open");
  expect(within(later).getByText("p.301")).toBeInTheDocument();
  const unknown = screen.getByRole("region", { name: "Not known when this arrived" });
  expect(within(unknown).getByText("p.150")).toBeInTheDocument();
  expect(within(unknown).getByText("We have not placed it before this visit.")).toBeInTheDocument();
});

test("names the guidance in force with its version and a link, and nothing else of it", async () => {
  inCase("launch");
  serve();
  open();
  const guidance = await screen.findByRole("region", { name: "Guidance in force that day" });
  expect(within(guidance).getByRole("link", { name: "Guidance title (G1)" })).toHaveAttribute("href", "https://example.test/g1");
  expect(within(guidance).getByText("2023 edition")).toBeInTheDocument();
});

test("saves your note", async () => {
  inCase("launch");
  const calls = serve(consultation({ note: "Earlier note" }));
  const { user } = open();
  const box = await screen.findByLabelText("Your note");
  expect(box).toHaveValue("Earlier note");
  await user.clear(box);
  await user.type(box, "Looks late");
  await user.click(screen.getByRole("button", { name: "Save note" }));
  await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ body: "Looks late" }));
  expect(await screen.findByText("Your note is saved.")).toBeInTheDocument();
});

test("changing when something arrived needs a reason", async () => {
  inCase("launch");
  const calls = serve();
  const { user } = open();
  await user.click(await screen.findByText("Show what came later (1)"));
  const later = screen.getByText("Show what came later (1)").closest("details")!;
  await user.click(within(later).getByRole("button", { name: "Change when this arrived" }));
  const dialog = await screen.findByRole("dialog", { name: "When did this arrive?" });
  const save = within(dialog).getByRole("button", { name: "Save" });
  await user.type(within(dialog).getByLabelText("Date it arrived"), "2024-05-11");
  expect(save).toBeDisabled();
  await user.type(within(dialog).getByLabelText("Why are you changing it?"), "Stamped by the surgery on the 11th");
  await user.click(save);
  await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ available_at: "2024-05-11", unknown: false, reason: "Stamped by the surgery on the 11th" }));
  expect((calls.find((c) => c.method === "PATCH")!.url.pathname)).toContain(FACT_3);
});

test("you can say it is not known when something arrived, with a reason", async () => {
  inCase("launch");
  const calls = serve();
  const { user } = open();
  const unknown = await screen.findByRole("region", { name: "Not known when this arrived" });
  await user.click(within(unknown).getByRole("button", { name: "Change when this arrived" }));
  const dialog = await screen.findByRole("dialog", { name: "When did this arrive?" });
  await user.click(within(dialog).getByLabelText("It is not known when it arrived"));
  await user.type(within(dialog).getByLabelText("Why are you changing it?"), "No stamp on the page");
  await user.click(within(dialog).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ available_at: null, unknown: true, reason: "No stamp on the page" }));
});

test("comments carry a warning that they may have to be shared, and you can add one", async () => {
  inCase("launch");
  const calls = serve();
  const { user } = open();
  const region = await screen.findByRole("region", { name: "Comments" });
  expect(within(region).getByText("Comments are working notes. They never go into your report, but they may have to be shared if the court asks.")).toBeInTheDocument();
  expect(await within(region).findByText("Ask about the earlier visit")).toBeInTheDocument();
  await user.type(within(region).getByLabelText("Add a comment"), "Check the letter");
  await user.click(within(region).getByRole("button", { name: "Add comment" }));
  await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ target_kind: "fact", target_id: FACT_1, body: "Check the letter" }));
});
