// S01 against its canvas board: the promise on the left, and the note about the code under the form.
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { signedOut } from "../../test/api";
import { renderAt } from "../../test/render";

test("the password step says a code comes next and how to get in without the phone", async () => {
  signedOut();
  renderAt("/sign-in");
  const note = await screen.findByRole("note");
  expect(note).toHaveTextContent("Next, we'll send a 6-digit code to your phone.");
  expect(note).toHaveTextContent("Your records stay private and are never used to train AI.");
  expect(screen.getByRole("heading", { level: 2, name: "Every page read. Every fact traced." })).toBeInTheDocument();
});
