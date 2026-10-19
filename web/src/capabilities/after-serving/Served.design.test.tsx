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

describe("the steps that belong to the experts and the trial bundle", () => {
  test("the timeline shows the experts' discussion and the trial bundle, each with its date and status", async () => {
    serve("launch", {
      hearings: [trial()],
      steps: {
        meetings: [{ id: "m1", type: "joint", discussion_on: "2027-02-10", sign_by: "2027-02-24", status: "signed" }],
        pack: { built_on: "2027-05-30", stale: false },
      },
    });
    renderAt(url);
    const next = await screen.findByRole("region", { name: "What happens next" });
    const talk = within(next).getByText("Experts' discussion").closest("li") as HTMLElement;
    expect(within(talk).getByText("10 February 2027 · Signed")).toBeInTheDocument();
    const bundle = within(next).getByText("Trial bundle").closest("li") as HTMLElement;
    expect(within(bundle).getByText("Built 30 May 2027")).toBeInTheDocument();
  });

  test("a trial bundle that needs rebuilding says so, and one not yet built says that", async () => {
    serve("launch", { hearings: [trial()], steps: { meetings: [], pack: { built_on: "2027-05-30", stale: true } } });
    renderAt(url);
    expect(await screen.findByText("Out of date. Build it again.")).toBeInTheDocument();
    expect(screen.queryByText("Experts' discussion")).not.toBeInTheDocument();
  });

  test("with no bundle yet the step says it is not built", async () => {
    serve("launch", { hearings: [trial()] });
    renderAt(url);
    expect(await screen.findByText("Not built yet")).toBeInTheDocument();
  });
});
