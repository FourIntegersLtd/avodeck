// S03 against its canvas board: the date sits above the greeting (the case rings are tested with Cases).
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { signedIn } from "../../test/api";
import { renderAt } from "../../test/render";

test("the date comes before the greeting", async () => {
  signedIn();
  renderAt("/today");
  // The loading screen has a heading of its own, so wait for the date: it is only on the loaded page.
  const date = await screen.findByTestId("today-date");
  const greeting = screen.getByRole("heading", { level: 1 });
  expect(date.compareDocumentPosition(greeting) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

