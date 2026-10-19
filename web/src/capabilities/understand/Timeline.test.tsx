// S11 The timeline: lanes and their words come from the API (the pack), every event shows its status,
// linked questions and any thing to look at, the filters go to the server, and 1,500 events draw in under a second.
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { TimelineView } from "./Timeline";
import {
  event,
  FACT_1,
  inCase,
  launchWords,
  Q_2,
  timeline,
  type PackRun,
} from "./testing";

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
    expect(
      await screen.findByRole("heading", { level: 2, name: "The timeline" }),
    ).toBeInTheDocument();
    const lanes = await screen.findAllByRole("region", { name: /^Lane / });
    expect(
      lanes.map(
        (l) =>
          l.getAttribute("aria-label") ??
          within(l).getByRole("heading").textContent,
      ),
    ).toEqual(["Lane one words", "Lane two words"]);
    if (run === "fixture")
      expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("each event shows its page, status, linked questions and thing to look at, and opens its consultation", async () => {
  inCase("launch");
  serve();
  renderAt(`/cases/${CASE_A}/timeline`);
  const second = (await screen.findByRole("region", {
    name: "Lane two words",
  })) as HTMLElement;
  const first = screen.getByRole("region", { name: "Lane one words" });
  // A dot's details show when it is pointed at or reached with the keyboard.
  for (const link of [
    ...within(second).getAllByRole("link"),
    ...within(first).getAllByRole("link"),
  ])
    act(() => link.focus());
  expect(within(second).getByText("Please check")).toBeInTheDocument();
  expect(within(second).getByText("Thing to look at")).toBeInTheDocument();
  expect(within(second).getByText("Linked to 2 questions")).toBeInTheDocument();
  expect(within(second).getByText("p.214")).toBeInTheDocument();
  expect(within(first).getByText("Checked by you")).toBeInTheDocument();
  expect(
    within(first).getByText("Not linked to a question"),
  ).toBeInTheDocument();
  expect(
    within(first).getByRole("link", { name: /Consultation/ }),
  ).toHaveAttribute("href", `/cases/${CASE_A}/consultation/${FACT_1}`);
});

test("events with no place on any line are listed apart", async () => {
  inCase("launch");
  serve(
    timeline({
      unplaced: [event({ kind: "letter", event_date: null, page_no: 301 })],
    }),
  );
  renderAt(`/cases/${CASE_A}/timeline`);
  const apart = await screen.findByRole("region", {
    name: "Not on a line yet",
  });
  expect(within(apart).getByText("p.301")).toBeInTheDocument();
});

test("the filters are sent to the server", async () => {
  inCase("launch");
  const seen = serve();
  const { user } = renderAt(`/cases/${CASE_A}/timeline`);
  await screen.findByRole("region", { name: "Lane one words" });
  await user.click(screen.getByRole("radio", { name: "Please check · 1" }));
  await waitFor(() =>
    expect(seen.at(-1)!.searchParams.get("status")).toBe("suggested"),
  );
  await user.selectOptions(screen.getByLabelText("Linked to"), Q_2);
  await waitFor(() =>
    expect(seen.at(-1)!.searchParams.get("question_id")).toBe(Q_2),
  );
  await user.type(screen.getByLabelText("From"), "2024-02-01");
  await waitFor(() =>
    expect(seen.at(-1)!.searchParams.get("from")).toBe("2024-02-01"),
  );
  await user.type(screen.getByLabelText("To"), "2024-08-31");
  await waitFor(() =>
    expect(seen.at(-1)!.searchParams.get("to")).toBe("2024-08-31"),
  );
});

test("1,500 events draw in under one second", () => {
  const many = Array.from({ length: 1500 }, (_, i) =>
    event({
      fact_id: `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
      page_no: i + 1,
      question_ids: i % 3 ? [] : [Q_2],
    }),
  );
  const lanes = [0, 1, 2, 3, 4].map((n) => ({
    key: `lane_${n}`,
    label: `Lane ${n}`,
    events: many.slice(n * 300, n * 300 + 300),
  }));
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

describe("the picture of the record", () => {
  test("the filter pills carry how much is checked, to check, to look at and not linked, and send the choice", async () => {
    inCase("launch");
    const seen = serve();
    const { user } = renderAt(`/cases/${CASE_A}/timeline`);
    const pills = await screen.findByRole("radiogroup", { name: "Show" });
    expect(
      within(pills).getByRole("radio", { name: "Everything" }),
    ).toBeChecked();
    expect(
      within(pills).getByRole("radio", { name: "Checked by you · 2" }),
    ).toBeInTheDocument();
    await user.click(
      within(pills).getByRole("radio", { name: "Thing to look at · 1" }),
    );
    await waitFor(() =>
      expect(seen.at(-1)!.searchParams.get("only")).toBe("things_to_look"),
    );
    await user.click(
      within(pills).getByRole("radio", {
        name: "Not linked to a question · 1",
      }),
    );
    await waitFor(() =>
      expect(seen.at(-1)!.searchParams.get("only")).toBe("not_linked"),
    );
    expect(seen.at(-1)!.searchParams.get("status")).toBeNull();
  });

  test("names the stretch of the record it covers", async () => {
    inCase("launch");
    serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    expect(await screen.findByText("Feb to May 2024")).toBeInTheDocument();
  });

  test.each<PackRun>(["launch", "fixture"])(
    "can show the record as it stood on a day, in the %s pack's words",
    async (run) => {
      inCase(run);
      const seen = serve();
      const { user } = renderAt(`/cases/${CASE_A}/timeline`);
      await user.type(
        await screen.findByLabelText("Seen on the day on"),
        "2024-03-01",
      );
      await waitFor(() =>
        expect(seen.at(-1)!.searchParams.get("as_seen_on")).toBe("2024-03-01"),
      );
    },
  );

  test("each line has an icon in its colour", async () => {
    inCase("launch");
    serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    const first = await screen.findByRole("region", { name: "Lane one words" });
    expect(within(first).getByTestId("lane-icon")).toHaveStyle({
      background: "#2a6f97",
    });
    expect(within(first).getByTestId("lane-icon")).toHaveTextContent("L");
  });

  test("the legend uses the pack's names for the kinds of record", async () => {
    inCase("launch");
    serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    const legend = await screen.findByRole("list", { name: "Kinds of record" });
    expect(within(legend).getByText("Source one words")).toBeInTheDocument();
    expect(within(legend).getByText("Source two words")).toBeInTheDocument();
  });

  test("each line says how many entries it holds, and each entry shows its words and question chips", async () => {
    inCase("launch");
    serve(timeline());
    renderAt(`/cases/${CASE_A}/timeline`);
    const second = (await screen.findByRole("region", {
      name: "Lane two words",
    })) as HTMLElement;
    expect(within(second).getByText("1 entry")).toBeInTheDocument();
    expect(await within(second).findByText("Q1")).toBeInTheDocument();
    expect(within(second).getByText("Q2")).toBeInTheDocument();
    expect(
      within(second).getAllByText("Seen in surgery").length,
    ).toBeGreaterThan(0);
  });

  test("shows the months across the record and can zoom to one month", async () => {
    inCase("launch");
    serve();
    const { user } = renderAt(`/cases/${CASE_A}/timeline`);
    const axis = await screen.findByRole("list", { name: "Months" });
    expect(within(axis).getByText("Feb 2024")).toBeInTheDocument();
    expect(within(axis).getByText("May")).toBeInTheDocument();
    const board = screen.getByTestId("timeline-board");
    expect(board).toHaveAttribute("data-zoom", "whole");
    await user.click(screen.getByRole("radio", { name: "One month" }));
    expect(board).toHaveAttribute("data-zoom", "month");
  });
});

describe("the board layout", () => {
  test("ends with the way on to the things we found, with their number", async () => {
    inCase("launch");
    serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    const on = await screen.findByRole("link", {
      name: "Look at the 1 thing we found",
    });
    expect(on).toHaveAttribute("href", `/cases/${CASE_A}/things-to-look-at`);
  });

  test("the legend names what a filled dot, a dashed dot and an amber dot mean", async () => {
    inCase("launch");
    serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    const legend = await screen.findByRole("list", {
      name: "What the dots mean",
    });
    expect(within(legend).getByText("Checked by you")).toBeInTheDocument();
    expect(within(legend).getByText("Please check")).toBeInTheDocument();
    expect(within(legend).getByText("Thing to look at")).toBeInTheDocument();
  });

  test("each dot carries its state and a visible date", async () => {
    inCase("launch");
    serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    const second = await screen.findByRole("region", {
      name: "Lane two words",
    });
    const dot = within(second)
      .getByRole("link", { name: /Result/ })
      .closest("li")!;
    expect(dot).toHaveAttribute("data-state", "look");
    expect(within(dot).getByText("19 Feb")).toBeInTheDocument();
    const first = screen.getByRole("region", { name: "Lane one words" });
    expect(
      within(first)
        .getByRole("link", { name: /Consultation/ })
        .closest("li"),
    ).toHaveAttribute("data-state", "checked");
  });

  test("the day switch is named in the pack's words and opens the day field", async () => {
    inCase("launch");
    const seen = serve();
    const { user } = renderAt(`/cases/${CASE_A}/timeline`);
    const toggle = await screen.findByRole("switch", {
      name: "Seen on the day on…",
    });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await user.type(screen.getByLabelText("Seen on the day on"), "2024-03-01");
    await waitFor(() =>
      expect(seen.at(-1)!.searchParams.get("as_seen_on")).toBe("2024-03-01"),
    );
    await user.click(toggle);
    await waitFor(() =>
      expect(seen.at(-1)!.searchParams.get("as_seen_on")).toBeNull(),
    );
  });

  test("dates and question sit behind one link, so the board stays as drawn", async () => {
    inCase("launch");
    serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    expect(
      await screen.findByText("Change the dates or question"),
    ).toBeInTheDocument();
  });
});

test("the way on to the things we found shows their number, and is not there when there are none", async () => {
  inCase("launch");
  serve();
  const first = renderAt(`/cases/${CASE_A}/timeline`);
  expect(
    await screen.findByRole("link", { name: "Look at the 1 thing we found" }),
  ).toHaveAttribute("href", `/cases/${CASE_A}/things-to-look-at`);
  first.unmount();
  serve(
    timeline({
      counts: {
        checked: 2,
        please_check: 1,
        things_to_look: 0,
        not_linked: 1,
        total: 3,
      },
    }),
  );
  renderAt(`/cases/${CASE_A}/timeline`);
  await screen.findByRole("region", { name: "Lane one words" });
  expect(
    screen.queryByRole("link", { name: /we found/ }),
  ).not.toBeInTheDocument();
});

test("a dot's link keeps its name as text a screen reader and magnifier can reach, not size-zero text", async () => {
  inCase("launch");
  serve();
  renderAt(`/cases/${CASE_A}/timeline`);
  const link = await screen.findAllByRole("link", { name: /Consultation/ });
  expect(link[0]!.querySelector(".av-visually-hidden")).not.toBeNull();
});

describe("lane icons", () => {
  test("a known lane key draws an icon, an unknown one the first letter", async () => {
    inCase("launch");
    serve(
      timeline({
        lanes: [
          { key: "results", label: "Zed words", events: [], colour: "#2a6f97" },
          {
            key: "lane_one",
            label: "Yak words",
            events: [],
            colour: "#2a6f97",
          },
        ],
      }),
    );
    renderAt(`/cases/${CASE_A}/timeline`);
    const icons = await screen.findAllByTestId("lane-icon");
    expect(icons[0]!.querySelector("svg")).not.toBeNull();
    expect(icons[1]!.querySelector("svg")).toBeNull();
    expect(icons[1]!.textContent).toBe("Y");
  });
});
