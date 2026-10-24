// S11 question highlight: pointing at a question's chip lights that question's entries and fades the rest;
// the side panel gives the keyboard the same thing as a toggle.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { event, FACT_2, inCase, Q_1, Q_2, timeline } from "./testing";

function serve() {
  const lanes = [
    { key: "lane_one", label: "Lane one words", events: [event({ question_ids: [Q_1] })], colour: "#2a6f97" },
    { key: "lane_two", label: "Lane two words", events: [event({ fact_id: FACT_2, kind: "result", page_no: 214, event_date: "2024-02-19", status: "suggested", question_ids: [Q_1, Q_2] })] },
  ];
  server.use(http.get("/v1/cases/:caseId/timeline", () => json(timeline({ lanes }))));
}

async function cards() {
  const result = await screen.findByRole("button", { name: /Result · 19 February 2024/ });
  const consult = screen.getByRole("button", { name: /Consultation · 15 May 2024/ });
  return { result, consult };
}

describe("question highlight", () => {
  test("hovering a card's Q2 chip lights Q2's cards, fades the others, and moving away clears it", async () => {
    inCase("launch");
    serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    const { result, consult } = await cards();
    const chip = within(result).getByText("Q2");
    fireEvent.mouseEnter(chip);
    expect(result).toHaveAttribute("data-highlight", "on");
    expect(consult).toHaveAttribute("data-faded", "on");
    expect(consult).not.toHaveAttribute("data-highlight");
    fireEvent.mouseLeave(chip);
    expect(result).not.toHaveAttribute("data-highlight");
    expect(consult).not.toHaveAttribute("data-faded");
  });

  test("a chip on a card is not a tab stop of its own", async () => {
    inCase("launch");
    serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    const { result } = await cards();
    expect(within(result).queryByRole("button", { name: /Q2/ })).toBeNull();
  });

  test("in the side panel the question chip is a toggle that lights and clears, and says so aloud", async () => {
    inCase("launch");
    serve();
    const { user } = renderAt(`/cases/${CASE_A}/timeline`);
    const { result, consult } = await cards();
    await user.click(result);
    const panel = await screen.findByRole("complementary", { name: "The entry you opened" });
    const toggle = within(panel).getByRole("button", { name: "Light up everything linked to Q2" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(result).toHaveAttribute("data-highlight", "on");
    expect(consult).toHaveAttribute("data-faded", "on");
    expect(document.querySelector('[aria-live="polite"]')).toHaveTextContent("Showing entries linked to Q2");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(consult).not.toHaveAttribute("data-faded");
    expect(document.querySelector('[aria-live="polite"]')).toBeEmptyDOMElement();
  });

  test("closing the panel clears the highlight", async () => {
    inCase("launch");
    serve();
    const { user } = renderAt(`/cases/${CASE_A}/timeline`);
    const { result, consult } = await cards();
    await user.click(result);
    const panel = await screen.findByRole("complementary", { name: "The entry you opened" });
    await user.click(within(panel).getByRole("button", { name: "Light up everything linked to Q2" }));
    expect(result).toHaveAttribute("data-highlight", "on");
    await user.click(within(panel).getByRole("button", { name: /^Close/ }));
    await waitFor(() => expect(screen.queryByRole("complementary", { name: "The entry you opened" })).toBeNull());
    expect(consult).not.toHaveAttribute("data-faded");
  });

  test("Escape with no panel open clears the highlight", async () => {
    inCase("launch");
    serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    const { result } = await cards();
    fireEvent.mouseEnter(within(result).getByText("Q2"));
    expect(result).toHaveAttribute("data-highlight", "on");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(result).not.toHaveAttribute("data-highlight");
  });

  test("passes axe with a highlight on", async () => {
    inCase("launch");
    serve();
    const { container } = renderAt(`/cases/${CASE_A}/timeline`);
    const { result } = await cards();
    fireEvent.mouseEnter(within(result).getByText("Q2"));
    expect(await axeViolations(container)).toEqual([]);
  });
});
