// S03 against its canvas board: the date sits above the greeting (the case rings are tested with Cases).
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { signedIn } from "../../test/api";
import { renderAt } from "../../test/render";

test("the date comes before the greeting", async () => {
  signedIn();
  renderAt("/today");
  const greeting = await screen.findByRole("heading", { level: 1 });
  const date = screen.getByTestId("today-date");
  expect(date.compareDocumentPosition(greeting) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

