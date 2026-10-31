// S03: the "How a case works" walkthrough: one step per click, from the pack's step keys.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { signedIn } from "../../test/api";
import { launchPack, PERSONAL_ORG, today } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";

const keys = launchPack.workflow_steps.map((s) => s.key);

async function emptyCases() {
  const { registerScreen } = await import("../../foundation/lib/screens");
  registerScreen("new_case", () => "/cases/new");
  signedIn({ today: today({ cases: [], needs_you: [], closed_count: 0, organisations: [{ id: PERSONAL_ORG, name: "Jane Morgan", kind: "personal", open_count: 0 }] }) });
  const view = renderAt("/cases");
  const trigger = await screen.findByRole("button", { name: "How a case works" });
  return { ...view, trigger };
}

test("the trigger is a labelled quiet button that opens a labelled modal dialog on step one", async () => {
  const { trigger, user } = await emptyCases();
  expect(trigger).not.toHaveAttribute("aria-haspopup", "menu");
  expect(screen.queryByRole("dialog")).toBeNull();
  await user.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "How a case works" });
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(within(dialog).getByText("Every case goes through the same five steps. You stay in charge at each one.")).toBeInTheDocument();
  expect(within(dialog).getByText("Step 1 of 5")).toBeInTheDocument();
  expect(within(dialog).getByRole("heading", { level: 3, name: "Prepare" })).toBeInTheDocument();
  expect(within(dialog).getByText("Add the letter and the records. We number every page and sort them by date.")).toBeInTheDocument();
});

test("exactly one step shows at a time and Next and Back move one step", async () => {
  const { trigger, user } = await emptyCases();
  await user.click(trigger);
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getAllByRole("heading", { level: 3 })).toHaveLength(1);
  expect(within(dialog).queryByRole("button", { name: "Back" })).toBeNull();
  await user.click(within(dialog).getByRole("button", { name: "Next" }));
  expect(within(dialog).getByText("Step 2 of 5")).toBeInTheDocument();
  expect(within(dialog).getByRole("heading", { level: 3, name: "Read" })).toBeInTheDocument();
  expect(within(dialog).queryByRole("heading", { level: 3, name: "Prepare" })).toBeNull();
  expect(within(dialog).getAllByRole("heading", { level: 3 })).toHaveLength(1);
  await user.click(within(dialog).getByRole("button", { name: "Back" }));
  expect(within(dialog).getByRole("heading", { level: 3, name: "Prepare" })).toBeInTheDocument();
});

test("the arrow keys step too", async () => {
  const { trigger, user } = await emptyCases();
  await user.click(trigger);
  const dialog = screen.getByRole("dialog");
  await user.keyboard("{ArrowRight}");
  expect(within(dialog).getByRole("heading", { level: 3, name: "Read" })).toBeInTheDocument();
  await user.keyboard("{ArrowRight}");
  expect(within(dialog).getByRole("heading", { level: 3, name: "Understand" })).toBeInTheDocument();
  await user.keyboard("{ArrowLeft}");
  expect(within(dialog).getByRole("heading", { level: 3, name: "Read" })).toBeInTheDocument();
});

test("the closing note shows only on the last step, whose button finishes", async () => {
  const { trigger, user } = await emptyCases();
  await user.click(trigger);
  const dialog = screen.getByRole("dialog");
  const note = "Avodeck never writes your opinion. It finds and links the facts. What you conclude, and how you say it, is always yours.";
  for (let i = 1; i < keys.length; i++) {
    expect(within(dialog).queryByText(note)).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Next" }));
  }
  expect(within(dialog).getByText(`Step ${keys.length} of ${keys.length}`)).toBeInTheDocument();
  expect(within(dialog).getByRole("heading", { level: 3, name: "Take it forward" })).toBeInTheDocument();
  expect(within(dialog).getByText(note)).toBeInTheDocument();
  expect(within(dialog).queryByRole("button", { name: "Next" })).toBeNull();
  await user.click(within(dialog).getByRole("button", { name: "Got it" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("Escape closes and focus returns to the trigger; focus starts inside and Tab stays inside", async () => {
  const { trigger, user } = await emptyCases();
  await user.click(trigger);
  const dialog = screen.getByRole("dialog");
  expect(dialog.contains(document.activeElement)).toBe(true);
  for (let i = 0; i < 8; i++) {
    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
  }
  await user.tab({ shift: true });
  expect(dialog.contains(document.activeElement)).toBe(true);
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(trigger).toHaveFocus();
});

test("the walkthrough has no accessibility violations", async () => {
  const { trigger, user } = await emptyCases();
  await user.click(trigger);
  expect(await axeViolations(document.body)).toEqual([]);
});

test("a person with cases gets the trigger beside Start a new case, steps from their case's pack", async () => {
  const { registerScreen } = await import("../../foundation/lib/screens");
  registerScreen("new_case", () => "/cases/new");
  const { caseCard } = await import("../../test/fixtures");
  signedIn({ today: today({ cases: [caseCard({ label: "Mrs A", stage: "read" })] }) });
  const { user } = renderAt("/cases");
  await user.click(await screen.findByRole("button", { name: "How a case works" }));
  expect(screen.getByText("Step 1 of 5")).toBeInTheDocument();
});
