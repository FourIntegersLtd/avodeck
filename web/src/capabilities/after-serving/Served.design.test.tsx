// S19 as designed: the "what happens next" timeline, the locked-version pill and four cards with the facts that matter.
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { launchWords } from "../setup/testing";
import { change, deadline, question, Q2, questionsName, serve, type PackRun } from "./testing";
import type { Schemas } from "../../foundation/platform-client";

const url = `/cases/${CASE_A}/served`;
const trial = (over: Partial<Schemas["HearingOut"]> = {}): Schemas["HearingOut"] => ({
  id: "h1", type: "trial", starts_on: "2027-06-14", ends_on: "2027-06-16", status: "scheduled", venue: "Court 4", direction_id: null, replaced_by_hearing_id: null, changed_at: null, version: 1, ...over,
});

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("the timeline lists the dates in order, the trial with its days, and passes axe", async () => {
    serve(run, { deadlines: [deadline({ kind: "written_questions", due_on: "2026-12-19", days_left: 18 })], hearings: [trial()] });
    const { container } = renderAt(url);
    const next = await screen.findByRole("region", { name: "What happens next" });
    expect(within(next).getByText("Report served")).toBeInTheDocument();
    expect(within(next).getByText("28 Nov")).toBeInTheDocument();
    expect(within(next).getByText("Due 19 Dec")).toBeInTheDocument();
    expect(within(next).getByText("From 14 June 2027 · 3 days")).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });

  test("the questions card counts the questions, says when they arrived and how many days are left", async () => {
    serve(run, { deadlines: [deadline({ days_left: 18 })], questions: [question(), question({ id: Q2, number: 2 })] });
    renderAt(url);
    expect(await screen.findByText(`2 ${questionsName(run)}`)).toBeInTheDocument();
    expect(screen.getByText("Received 1 December. 18 days left to answer.")).toBeInTheDocument();
  });
});

test("the other cards say what arrived, what has changed and what the court has ordered", async () => {
  serve("launch", {
    pages: Array.from({ length: 14 }, (_, i) => ({ page_id: `p${i}`, page_no: 1232 + i, read: false, may_matter: [] })),
    changes: [change()],
    hearings: [trial()],
    directions: [{ id: "x", made_on: "2027-01-20", reference: null, text: null, stay: false, document_id: null, created_deadline_ids: [], created_at: "2027-01-20T10:00:00Z" }],
  });
  renderAt(url);
  expect(await screen.findByText("14 new pages have arrived.")).toBeInTheDocument();
  expect(screen.getByText("1 change recorded since serving.")).toBeInTheDocument();
  expect(screen.getByText("1 order recorded. Trial listed for 14 June 2027.")).toBeInTheDocument();
});

test("the locked version sits in a pill at the top, with the paragraphs behind View", async () => {
  serve("launch");
  const { user } = renderAt(url);
  const pill = await screen.findByRole("group", { name: "Locked version" });
  await user.click(within(pill).getByRole("button", { name: "View" }));
  expect(await screen.findByText("Mrs A should have been referred in early March 2024.")).toBeInTheDocument();
});
