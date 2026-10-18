// S11 The timeline: lanes and their words come from the API (the pack), every event shows its status,
// linked questions and any thing to look at, the filters go to the server, and 1,500 events draw in under a second.
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { TimelineView } from "./Timeline";
import { event, FACT_1, inCase, launchWords, Q_2, timeline, type PackRun } from "./testing";

function serve(body = timeline()) {
  const seen: URL[] = [];
  server.use(
    http.get("/v1/cases/:caseId/timeline", ({ request }) => {
      seen.push(new URL(request.url));
      return json(body);
    }),
  );
  return seen;
}

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("shows the lanes in the order and words the API gives, and passes axe", async () => {
    inCase(run);
    serve();
    const { container } = renderAt(`/cases/${CASE_A}/timeline`);
    expect(await screen.findByRole("heading", { level: 2, name: "The timeline" })).toBeInTheDocument();
    const lanes = await screen.findAllByRole("region", { name: /^Lane / });
    expect(lanes.map((l) => l.getAttribute("aria-label") ?? within(l).getByRole("heading").textContent)).toEqual(["Lane one words", "Lane two words"]);
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("each event shows its page, status, linked questions and thing to look at, and opens its consultation", async () => {
  inCase("launch");
  serve();
  renderAt(`/cases/${CASE_A}/timeline`);
  const second = (await screen.findByRole("region", { name: "Lane two words" })) as HTMLElement;
  expect(within(second).getByText("Please check")).toBeInTheDocument();
  expect(within(second).getByText("Thing to look at")).toBeInTheDocument();
  expect(within(second).getByText("Linked to 2 questions")).toBeInTheDocument();
  expect(within(second).getByText("p.214")).toBeInTheDocument();
  const first = screen.getByRole("region", { name: "Lane one words" });
  expect(within(first).getByText("Checked by you")).toBeInTheDocument();
  expect(within(first).getByText("Not linked to a question")).toBeInTheDocument();
  expect(within(first).getByRole("link", { name: /Consultation/ })).toHaveAttribute("href", `/cases/${CASE_A}/consultation/${FACT_1}`);
});

test("events with no place on any line are listed apart", async () => {
  inCase("launch");
  serve(timeline({ unplaced: [event({ kind: "letter", event_date: null, page_no: 301 })] }));
  renderAt(`/cases/${CASE_A}/timeline`);
  const apart = await screen.findByRole("region", { name: "Not on a line yet" });
  expect(within(apart).getByText("p.301")).toBeInTheDocument();
});

test("the filters are sent to the server", async () => {
  inCase("launch");
  const seen = serve();
  const { user } = renderAt(`/cases/${CASE_A}/timeline`);
  await screen.findByRole("region", { name: "Lane one words" });
  await user.selectOptions(screen.getByLabelText("Show"), "suggested");
  await waitFor(() => expect(seen.at(-1)!.searchParams.get("status")).toBe("suggested"));
  await user.selectOptions(screen.getByLabelText("Linked to"), Q_2);
  await waitFor(() => expect(seen.at(-1)!.searchParams.get("question_id")).toBe(Q_2));
  await user.type(screen.getByLabelText("From"), "2024-02-01");
  await waitFor(() => expect(seen.at(-1)!.searchParams.get("from")).toBe("2024-02-01"));
  await user.type(screen.getByLabelText("To"), "2024-08-31");
  await waitFor(() => expect(seen.at(-1)!.searchParams.get("to")).toBe("2024-08-31"));
});

test("1,500 events draw in under one second", () => {
  const many = Array.from({ length: 1500 }, (_, i) => event({ fact_id: `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`, page_no: i + 1, question_ids: i % 3 ? [] : [Q_2] }));
  const lanes = [0, 1, 2, 3, 4].map((n) => ({ key: `lane_${n}`, label: `Lane ${n}`, events: many.slice(n * 300, n * 300 + 300) }));
  const started = performance.now();
  render(
    <MemoryRouter>
      <TimelineView caseId={CASE_A} data={{ lanes, unplaced: [] }} />
    </MemoryRouter>,
  );
  const took = performance.now() - started;
  expect(screen.getAllByRole("listitem").length).toBeGreaterThanOrEqual(1500);
  expect(took).toBeLessThan(1000);
});
