// S11 The timeline: a ribbon across the whole record with a window the expert moves, a board of lanes from the
// pack where each day's entries share one card, and a side panel that opens any entry without leaving the screen.
// Lanes and their words come from the API (the pack); filters go to the server; 1,500 events draw in under a second.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { entryHref } from "./shared";
import { splitForTrack } from "./Timeline";
import { TimelineBoard } from "./TimelineBoard";
import { event, FACT_1, FACT_2, inCase, launchWords, overview, Q_2, timeline, type PackRun } from "./testing";

let seenOverviews: URL[] = [];

function serve(body = timeline()) {
  const seen: URL[] = [];
  const overviews: URL[] = [];
  server.use(
    http.get("/v1/cases/:caseId/timeline", ({ request }) => {
      seen.push(new URL(request.url));
      return json(body);
    }),
    http.get("/v1/cases/:caseId/timeline/overview", ({ request }) => {
      overviews.push(new URL(request.url));
      return json(overview());
    }),
  );
  seenOverviews = overviews;
  return seen;
}

/** The board's own requests carry the window; the ribbon's ask for the whole record. */
const boardCalls = (seen: URL[]) => seen.filter((u) => u.searchParams.has("from") || u.searchParams.has("status") || u.searchParams.has("only") || u.searchParams.has("question_id"));

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("the board is the first view, shows the lanes in the API's order and words, and passes axe", async () => {
    inCase(run);
    serve();
    const { container } = renderAt(`/cases/${CASE_A}/timeline`);
    expect(await screen.findByRole("heading", { level: 2, name: "The timeline" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Board" })).toBeChecked();
    await screen.findByTestId("timeline-board");
    const lanes = await screen.findAllByRole("region", { name: /^Lane / });
    expect(lanes.map((l) => within(l).getByRole("heading").textContent)).toEqual(["Lane one words", "Lane two words"]);
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });

  test("the side panel passes axe", async () => {
    inCase(run);
    serve();
    const { container, user } = renderAt(`/cases/${CASE_A}/timeline`);
    await user.click(await screen.findByRole("button", { name: /Consultation · 15 May 2024/ }));
    await screen.findByRole("complementary", { name: "The entry you opened" });
    expect(await axeViolations(container)).toEqual([]);
  });
});

describe("the board", () => {
  test("each entry is a card with its date, words, status and question chips", async () => {
    inCase("launch");
    serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    const second = await screen.findByRole("region", { name: "Lane two words" });
    const card = within(second).getByRole("button", { name: /Result · 19 February 2024/ });
    expect(card).toHaveAttribute("data-state", "look");
    expect(within(card).getByText("19 Feb")).toBeInTheDocument();
    expect(within(card).getByText("Seen in surgery")).toBeInTheDocument();
    expect(within(card).getByText("Q1")).toBeInTheDocument();
    expect(within(card).getByText("Q2")).toBeInTheDocument();
    const first = screen.getByRole("region", { name: "Lane one words" });
    expect(within(first).getByRole("button", { name: /Consultation · 15 May 2024/ })).toHaveAttribute("data-state", "checked");
  });

  test("entries in one lane on the same day share one card that says how many there are", async () => {
    inCase("launch");
    const sameDay = Array.from({ length: 5 }, (_, i) => event({ fact_id: `a1111111-1111-4111-8111-11111111110${i}`, kind: "result", event_date: "2024-05-10", summary: `Result ${i}` }));
    serve(timeline({ lanes: [{ key: "lane_one", label: "Lane one words", events: sameDay, colour: "#2a6f97" }] }));
    renderAt(`/cases/${CASE_A}/timeline`);
    const lane = await screen.findByRole("region", { name: "Lane one words" });
    const cards = within(lane).getAllByRole("button", { name: /10 May 2024/ });
    expect(cards).toHaveLength(1);
    expect(within(cards[0]!).getByText("5 entries")).toBeInTheDocument();
    expect(within(lane).getByText("5 entries in this period")).toBeInTheDocument();
  });

  test("a crowded lane folds what does not fit into a counted mark that opens them in the side panel", async () => {
    inCase("launch");
    const busy = Array.from({ length: 40 }, (_, i) => event({ fact_id: `a1111111-1111-4111-8111-1111111111${String(i).padStart(2, "0")}`, event_date: `2024-05-${String((i % 28) + 1).padStart(2, "0")}`, available_at: null, summary: `Entry ${i}` }));
    serve(timeline({ lanes: [{ key: "lane_one", label: "Lane one words", events: busy, colour: "#2a6f97" }] }));
    const { user } = renderAt(`/cases/${CASE_A}/timeline`);
    const lane = await screen.findByRole("region", { name: "Lane one words" });
    const more = within(lane).getAllByRole("button", { name: /^\d+ more entries/ });
    expect(more.length).toBeGreaterThan(0);
    await user.click(more[0]!);
    const panel = await screen.findByRole("complementary", { name: "The entry you opened" });
    expect(within(panel).getAllByRole("button", { name: /^Open .* · / }).length).toBeGreaterThan(1);
    expect(screen.getByText(/^Showing [\d,]+ of 40 entries/)).toBeInTheDocument();
  });

  test("a lane with nothing in the window stays, and says so", async () => {
    inCase("launch");
    serve(timeline({ lanes: [{ key: "lane_one", label: "Lane one words", events: [event()], colour: "#2a6f97" }, { key: "lane_two", label: "Lane two words", events: [] }] }));
    renderAt(`/cases/${CASE_A}/timeline`);
    const empty = await screen.findByRole("region", { name: "Lane two words" });
    expect(within(empty).getByText("Nothing in this period")).toBeInTheDocument();
  });

  test("a thing to look at shows how long until the next entry", async () => {
    inCase("launch");
    serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    expect(await screen.findByText("86 days to the next entry")).toBeInTheDocument();
  });

  test("names the stretch shown and how detailed the view is", async () => {
    inCase("launch");
    serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    const board = await screen.findByTestId("timeline-board");
    expect(board).toHaveAttribute("data-level", "months");
    const axis = within(board).getByRole("list", { name: "Months" });
    expect(within(axis).getByText("Feb 2024")).toBeInTheDocument();
    expect(within(axis).getByText("May")).toBeInTheDocument();
  });

  test("each line has an icon in its colour, and the legends use the pack's names", async () => {
    inCase("launch");
    serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    const first = await screen.findByRole("region", { name: "Lane one words" });
    expect(within(first).getByTestId("lane-icon")).toHaveStyle({ background: "#2a6f97" });
    expect(within(first).getByTestId("lane-icon")).toHaveTextContent("L");
    const kinds = screen.getByRole("list", { name: "Kinds of record" });
    expect(within(kinds).getByText("Source one words")).toBeInTheDocument();
    const dots = screen.getByRole("list", { name: "What the dots mean" });
    expect(within(dots).getByText("Checked by you")).toBeInTheDocument();
    expect(within(dots).getByText("Please check")).toBeInTheDocument();
    expect(within(dots).getByText("Thing to look at")).toBeInTheDocument();
  });

  test("a lane whose pack icon this screen has draws it; any other lane draws its first letter", async () => {
    inCase("launch");
    serve(timeline({ lanes: [
      { key: "lane_one", label: "Zed words", events: [event({ fact_id: "r1", event_date: "2024-02-01" })], colour: "#2a6f97", icon: "flask" },
      { key: "lane_two", label: "Yak words", events: [event({ fact_id: "y1", event_date: "2024-03-01" })], colour: "#2a6f97", icon: null },
    ] }));
    renderAt(`/cases/${CASE_A}/timeline`);
    const icons = await screen.findAllByTestId("lane-icon");
    expect(icons[0]!.querySelector("svg")).not.toBeNull();
    expect(icons[1]!.querySelector("svg")).toBeNull();
    expect(icons[1]!.textContent).toBe("Y");
  });

  test("entries with no place on any line are listed apart", async () => {
    inCase("launch");
    serve(timeline({ unplaced: [event({ kind: "letter", event_date: null, available_at: null, page_no: 301 })] }));
    renderAt(`/cases/${CASE_A}/timeline`);
    const apart = await screen.findByRole("region", { name: "Not on a line yet" });
    expect(within(apart).getByText("p.301")).toBeInTheDocument();
  });

  test("ends with the way on to the things we found, and not when there are none", async () => {
    inCase("launch");
    serve();
    const first = renderAt(`/cases/${CASE_A}/timeline`);
    expect(await screen.findByRole("link", { name: "Look at the 1 thing we found" })).toHaveAttribute("href", `/cases/${CASE_A}/things-to-look-at`);
    first.unmount();
    serve(timeline({ counts: { checked: 2, please_check: 1, things_to_look: 0, not_linked: 1, undated: 0, total: 3 } }));
    renderAt(`/cases/${CASE_A}/timeline`);
    await screen.findByRole("region", { name: "Lane one words" });
    expect(screen.queryByRole("link", { name: /we found/ })).not.toBeInTheDocument();
  });

  test("shows a banner and link to Read when the calendar is empty but entries lack dates", async () => {
    inCase("launch");
    serve(timeline({
      lanes: [{ key: "lane_0", label: "Lane one words", events: [event({ event_date: null, available_at: null })] }],
      unplaced: [],
      counts: { checked: 1, please_check: 0, things_to_look: 0, not_linked: 1, undated: 1, total: 1 },
    }));
    renderAt(`/cases/${CASE_A}/timeline`);
    const banner = await screen.findByTestId("timeline-undated-banner");
    expect(banner).toHaveTextContent(/no confirmed date yet/);
    expect(within(banner).getByRole("link", { name: "Go to Read" }).getAttribute("href")).toContain(`/cases/${CASE_A}`);
  });
});

describe("the side panel", () => {
  test("opening a card shows the entry, its page and the ways on, and puts it in the address", async () => {
    inCase("launch");
    serve();
    const { user, router } = renderAt(`/cases/${CASE_A}/timeline`);
    await user.click(await screen.findByRole("button", { name: /Result · 19 February 2024/ }));
    const panel = await screen.findByRole("complementary", { name: "The entry you opened" });
    expect(within(panel).getByRole("heading", { name: "Result · 19 February 2024" })).toBeInTheDocument();
    expect(within(panel).getByText("Please check")).toBeInTheDocument();
    expect(within(panel).getByText("Thing to look at")).toBeInTheDocument();
    expect(within(panel).getByText("Linked to 2 questions")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Open page 214 in Read" })).toHaveAttribute("href", `/cases/${CASE_A}/read/214?fact=${FACT_2}`);
    expect(within(panel).getByRole("link", { name: "Open this day as it was seen" })).toHaveAttribute("href", entryHref(CASE_A, event({ fact_id: FACT_2, event_date: "2024-02-19" })));
    expect(router.state.location.search).toContain(`entry=${FACT_2}`);
  });

  test("steps to the later and earlier entry across lanes", async () => {
    inCase("launch");
    serve();
    const { user } = renderAt(`/cases/${CASE_A}/timeline`);
    await user.click(await screen.findByRole("button", { name: /Result · 19 February 2024/ }));
    const panel = await screen.findByRole("complementary", { name: "The entry you opened" });
    await user.click(within(panel).getByRole("button", { name: "Later entry" }));
    expect(await within(panel).findByRole("heading", { name: "Consultation · 15 May 2024" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Later entry" })).toBeDisabled();
    await user.click(within(panel).getByRole("button", { name: "Earlier entry" }));
    expect(await within(panel).findByRole("heading", { name: "Result · 19 February 2024" })).toBeInTheDocument();
  });

  test("Escape closes it and puts focus back on the card", async () => {
    inCase("launch");
    serve();
    const { user, router } = renderAt(`/cases/${CASE_A}/timeline`);
    const card = await screen.findByRole("button", { name: /Consultation · 15 May 2024/ });
    await user.click(card);
    await screen.findByRole("complementary", { name: "The entry you opened" });
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("complementary", { name: "The entry you opened" })).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Consultation · 15 May 2024/ })).toHaveFocus();
    expect(router.state.location.search).not.toContain("entry=");
  });

  test("an address with an entry opens it straight away", async () => {
    inCase("launch");
    serve();
    renderAt(`/cases/${CASE_A}/timeline?entry=${FACT_1}`);
    const panel = await screen.findByRole("complementary", { name: "The entry you opened" });
    expect(within(panel).getByRole("heading", { name: "Consultation · 15 May 2024" })).toBeInTheDocument();
  });

  test("an undated entry opens Read, with no day to open", async () => {
    inCase("launch");
    const undatedId = "00000000-0000-4000-8000-000000000099";
    serve(timeline({ lanes: [{ key: "lane_0", label: "Lane one words", events: [] }], unplaced: [event({ fact_id: undatedId, event_date: null, available_at: null, page_no: 301 })] }));
    const { user } = renderAt(`/cases/${CASE_A}/timeline`);
    const apart = await screen.findByRole("region", { name: "Not on a line yet" });
    await user.click(within(apart).getByRole("button", { name: /Consultation · No date/ }));
    const panel = await screen.findByRole("complementary", { name: "The entry you opened" });
    expect(within(panel).getByRole("link", { name: "Open page 301 in Read" })).toHaveAttribute("href", `/cases/${CASE_A}/read/301?fact=${undatedId}`);
    expect(within(panel).queryByRole("link", { name: "Open this day as it was seen" })).not.toBeInTheDocument();
  });
});

describe("the ribbon and the window", () => {
  test("the instruction period is the first window, sent as from and to", async () => {
    inCase("launch");
    const seen = serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    await screen.findByTestId("timeline-ribbon");
    await waitFor(() => {
      const last = boardCalls(seen).at(-1)!;
      expect(last.searchParams.get("from")).toBe("2024-02-01");
      expect(last.searchParams.get("to")).toBe("2024-08-31");
    });
    expect(screen.getByRole("radio", { name: "Instruction period" })).toBeChecked();
  });

  test("the ribbon asks for the whole record, whatever the window", async () => {
    inCase("launch");
    const seen = serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    await screen.findByTestId("timeline-ribbon");
    await waitFor(() => expect(seenOverviews.length).toBeGreaterThan(0));
    expect(seenOverviews.every((u) => !u.searchParams.has("from") && !u.searchParams.has("to"))).toBe(true);
    // The board's first ask goes out before the window is seeded; once it is, every board request carries it.
    await waitFor(() => expect(seen.at(-1)!.searchParams.has("from")).toBe(true));
  });

  test("the window's two ends are sliders that read their dates and move a month with the arrow keys", async () => {
    inCase("launch");
    const seen = serve();
    renderAt(`/cases/${CASE_A}/timeline`);
    const start = await screen.findByRole("slider", { name: "Start of the window" });
    const end = screen.getByRole("slider", { name: "End of the window" });
    await waitFor(() => expect(start).toHaveAttribute("aria-valuetext", "1 February 2024"));
    expect(end).toHaveAttribute("aria-valuetext", "31 August 2024");
    act(() => start.focus());
    fireEvent.keyDown(start, { key: "ArrowRight" });
    await waitFor(() => expect(boardCalls(seen).at(-1)!.searchParams.get("from")).toBe("2024-03-01"));
    expect(start).toHaveAttribute("aria-valuetext", "1 March 2024");
    expect(screen.getByRole("radio", { name: "Choose dates" })).toBeChecked();
  });

  test("Whole record clears the window, and Choose dates offers the two date fields", async () => {
    inCase("launch");
    const seen = serve();
    const { user } = renderAt(`/cases/${CASE_A}/timeline`);
    await screen.findByTestId("timeline-ribbon");
    await user.click(screen.getByRole("radio", { name: "Whole record" }));
    await waitFor(() => {
      const last = seen.at(-1)!;
      expect(last.searchParams.get("from")).toBeNull();
      expect(last.searchParams.get("to")).toBeNull();
    });
    await user.click(screen.getByRole("radio", { name: "Choose dates" }));
    const from = screen.getByLabelText("From");
    await user.clear(from);
    await user.type(from, "2024-03-01");
    await waitFor(() => expect(boardCalls(seen).at(-1)!.searchParams.get("from")).toBe("2024-03-01"));
  });
});

describe("filters", () => {
  test("the status pills carry their counts and send the choice", async () => {
    inCase("launch");
    const seen = serve();
    const { user } = renderAt(`/cases/${CASE_A}/timeline`);
    const pills = await screen.findByRole("radiogroup", { name: "Show" });
    expect(within(pills).getByRole("radio", { name: "Everything" })).toBeChecked();
    expect(within(pills).getByRole("radio", { name: "Checked by you · 2" })).toBeInTheDocument();
    await user.click(within(pills).getByRole("radio", { name: "Please check · 1" }));
    await waitFor(() => expect(seen.at(-1)!.searchParams.get("status")).toBe("suggested"));
    await user.click(within(pills).getByRole("radio", { name: "Thing to look at · 1" }));
    await waitFor(() => expect(seen.at(-1)!.searchParams.get("only")).toBe("things_to_look"));
    await user.click(within(pills).getByRole("radio", { name: "Not linked to a question · 1" }));
    await waitFor(() => expect(seen.at(-1)!.searchParams.get("only")).toBe("not_linked"));
    expect(seen.at(-1)!.searchParams.get("status")).toBeNull();
  });

  test("the question pills send the question", async () => {
    inCase("launch");
    const seen = serve();
    const { user } = renderAt(`/cases/${CASE_A}/timeline`);
    const pills = await screen.findByRole("radiogroup", { name: "Linked to" });
    expect(within(pills).getByRole("radio", { name: "Any question" })).toBeChecked();
    await user.click(await within(pills).findByRole("radio", { name: "Q2" }));
    await waitFor(() => expect(seen.at(-1)!.searchParams.get("question_id")).toBe(Q_2));
  });

  test.each<PackRun>(["launch", "fixture"])("the day switch is named in the %s pack's words and opens the day field", async (run) => {
    inCase(run);
    const seen = serve();
    const { user } = renderAt(`/cases/${CASE_A}/timeline`);
    const toggle = await screen.findByRole("switch", { name: "Seen on the day on…" });
    expect(screen.queryByLabelText("Seen on the day on")).not.toBeInTheDocument();
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await user.type(screen.getByLabelText("Seen on the day on"), "2024-03-01");
    await waitFor(() => expect(seen.at(-1)!.searchParams.get("as_seen_on")).toBe("2024-03-01"));
    await user.click(toggle);
    await waitFor(() => expect(seen.at(-1)!.searchParams.get("as_seen_on")).toBeNull());
  });
});

describe("the list", () => {
  test("lists entries in date order under month headings, and each opens the side panel", async () => {
    inCase("launch");
    serve();
    const { user } = renderAt(`/cases/${CASE_A}/timeline`);
    await user.click(await screen.findByRole("radio", { name: "List" }));
    const list = await screen.findByTestId("timeline-chronology");
    expect(within(list).getAllByRole("heading").map((h) => h.textContent)).toEqual(["February 2024", "May 2024"]);
    const rows = within(list).getAllByRole("button", { name: /^Open / });
    expect(rows[0]).toHaveAccessibleName(/Result · 19 February 2024/);
    await user.click(rows[0]!);
    const panel = await screen.findByRole("complementary", { name: "The entry you opened" });
    expect(within(panel).getByRole("heading", { name: "Result · 19 February 2024" })).toBeInTheDocument();
  });

  test("the list passes axe", async () => {
    inCase("fixture");
    serve();
    const { container, user } = renderAt(`/cases/${CASE_A}/timeline`);
    await user.click(await screen.findByRole("radio", { name: "List" }));
    await screen.findByTestId("timeline-chronology");
    expect(await axeViolations(container)).toEqual([]);
  });
});

test("lane entries without a date are listed apart, not stacked on the left of the track", () => {
  const undatedId = "00000000-0000-4000-8000-000000000099";
  const split = splitForTrack(timeline({
    lanes: [{ key: "lane_0", label: "Lane one words", events: [event(), event({ fact_id: undatedId, event_date: null, available_at: null, page_no: 301 })] }],
    unplaced: [],
  }));
  expect(split.lanes[0]!.events).toHaveLength(1);
  expect(split.unplaced[0]!.page_no).toBe(301);
});

test("entryHref sends dated entries to consultation and undated to Read", () => {
  expect(entryHref(CASE_A, event())).toBe(`/cases/${CASE_A}/consultation/${FACT_1}`);
  expect(entryHref(CASE_A, event({ event_date: null, available_at: null, page_no: 8 }))).toBe(`/cases/${CASE_A}/read/8?fact=${FACT_1}`);
});

test("1,500 events draw in under one second, with a bounded number of cards and an honest count", () => {
  const many = Array.from({ length: 1500 }, (_, i) =>
    event({ fact_id: `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`, page_no: i + 1, event_date: `2024-${String((i % 7) + 2).padStart(2, "0")}-${String((i % 27) + 1).padStart(2, "0")}`, question_ids: i % 3 ? [] : [Q_2] }),
  );
  const lanes = [0, 1, 2, 3, 4].map((n) => ({ key: `lane_${n}`, label: `Lane ${n}`, events: many.slice(n * 300, n * 300 + 300) }));
  const started = performance.now();
  render(
    <MemoryRouter>
      <TimelineBoard caseId={CASE_A} data={{ lanes, unplaced: [] }} from="2024-02-01" to="2024-08-31" onOpen={() => undefined} />
    </MemoryRouter>,
  );
  const took = performance.now() - started;
  expect(took).toBeLessThan(1000);
  expect(screen.getAllByRole("button").length).toBeLessThan(400);
  expect(screen.getByText(/^Showing [\d,]+ of 1,500 entries/)).toBeInTheDocument();
});
