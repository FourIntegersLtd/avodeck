// S20 as designed: the days-left chip, the question list beside the question in hand, the about line and the note
// for a question that goes beyond the report.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { deadline, question, Q2, serve } from "./testing";

const url = `/cases/${CASE_A}/written-questions`;

test("the chip counts the days left and names the set and the day", async () => {
  serve("launch", { deadlines: [deadline({ due_on: "2026-12-19", days_left: 18 })] });
  const { container } = renderAt(url);
  expect(await screen.findByText("18")).toBeInTheDocument();
  expect(screen.getByText("days left")).toBeInTheDocument();
  expect(screen.getByText("First set · due Saturday 19 December")).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
});

test("a list of the questions beside them shows each one's number, state and first words", async () => {
  serve("launch", { questions: [question({ status: "answering" }), question({ id: Q2, number: 2, text_verbatim: "What would a colonoscopy have shown?" })] });
  renderAt(url);
  const list = await screen.findByRole("navigation", { name: "Questions" });
  const first = within(list).getByRole("link", { name: /Question 1/ });
  expect(first).toHaveAttribute("href", "#question-1");
  expect(within(first).getByText("Being answered")).toBeInTheDocument();
  expect(within(list).getByRole("link", { name: /What would a colonoscopy have shown\?/ })).toHaveAttribute("href", "#question-2");
});

test("what a question is about reads as a line of its own", async () => {
  serve("launch");
  renderAt(url);
  expect(await screen.findByText("About:")).toBeInTheDocument();
  expect(screen.getByText("Paragraphs 6.12 to 6.14")).toBeInTheDocument();
});

test("a question that goes beyond the report gets a note and the way to tell the instructing party", async () => {
  serve("launch", { questions: [question({ id: Q2, number: 2, suggested_classification: "goes_beyond" })] });
  renderAt(url);
  const note = await screen.findByRole("note", { name: "About question 2" });
  expect(within(note).getByText(/The rules let you raise this rather than answer\./)).toBeInTheDocument();
  expect(within(note).getByRole("button", { name: /^Tell the / })).toBeInTheDocument();
});
