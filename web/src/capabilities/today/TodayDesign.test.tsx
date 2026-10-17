// S03 against its canvas board: the date sits above the greeting, and each case card shows how far
// along it is as a ring named with the pack's step.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { signedIn } from "../../test/api";
import { caseCard, launchPack, today } from "../../test/fixtures";
import { renderAt } from "../../test/render";

const total = launchPack.workflow_steps.length;
const position = launchPack.workflow_steps.findIndex((s) => s.key === "read") + 1;

test("the date comes before the greeting", async () => {
  signedIn();
  renderAt("/today");
  const greeting = await screen.findByRole("heading", { level: 1 });
  const date = screen.getByTestId("today-date");
  expect(date.compareDocumentPosition(greeting) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("each case card shows its step as a ring", async () => {
  signedIn({ today: today({ cases: [caseCard({ stage: "read" })] }) });
  renderAt("/today");
  const card = await screen.findByRole("link", { name: /Mrs A/ });
  const ring = await within(card).findByRole("img", { name: `Read, step ${position} of ${total}` });
  expect(ring).toBeInTheDocument();
});
