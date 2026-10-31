// S01 against its canvas board: the promise on the left, and the note about the code under the form.
import { screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, json, server, signedOut } from "../../test/api";
import { renderAt } from "../../test/render";

test("the password step says when the code from the phone is asked for and how to get in without the phone", async () => {
  signedOut();
  renderAt("/sign-in");
  const note = await screen.findByRole("note");
  expect(note).toHaveTextContent("We only ask for the code from your phone before important steps, such as signing or sending a report.");
  expect(note).not.toHaveTextContent("Next, we'll send");
  expect(note).toHaveTextContent("Your records stay private and are never used to train AI.");
  expect(screen.getByRole("heading", { level: 2, name: "Every page read. Every fact traced." })).toBeInTheDocument();
});

test("the privacy note says where records are stored when the cell names the place, and falls back when it does not", async () => {
  signedOut();
  server.use(http.get("/v1/cell", () => json({ storage_place: "Placeland" })));
  renderAt("/sign-in");
  const note = await screen.findByRole("note");
  await waitFor(() => expect(note).toHaveTextContent("Your records are stored in Placeland and never used to train AI."));
  expect(note).not.toHaveTextContent("stay private");
});
