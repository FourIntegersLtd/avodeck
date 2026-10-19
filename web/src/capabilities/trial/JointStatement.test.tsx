// S22 Joint statement: setting up the meeting with the pack's meeting types, the agenda questions with both
// positions, a changed position asking for a note, the pack's declarations, preparing and downloading for
// signing (behind the code), and a signed statement shown read-only. The meeting label comes from the pack.
import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { axeViolations, renderAt } from "../../test/render";
import { blobReply, CASE, item, ITEMS, launchWords, MEETING, MEETING_TYPE, meetingOut, pdf, serveMeeting, term, type PackRun } from "./testing";
import { TRIAL_MESSAGES as M } from "./words";

const url = `/cases/${CASE}/joint-statement`;
const k = (key: string) => M[`trial.${key}`]!;

const rowOf = (n: number) => screen.findByRole("row", { name: (name) => name.includes(`${n}. ${item(n as 1 | 2 | 3).text}`) });
const editor = (n: number) => screen.findByRole("group", { name: `${k("meeting.item.question").replace("{{number}}", String(n))}: ${item(n as 1 | 2 | 3).text}` });

beforeEach(() => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:statement"), revokeObjectURL: vi.fn() }));
});
afterEach(() => vi.unstubAllGlobals());

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("with no meeting yet, it offers the pack's meeting types and sets one up", async () => {
    const { sent, record } = serveMeeting(run, { meeting: null });
    server.use(http.post("/v1/cases/:caseId/expert-meetings", async ({ request }) => (await record(request), json(meetingOut(), 201))));
    const { user, container } = renderAt(url);
    // Wait for the screen's own heading: while the session loads, the shell shows a hidden one of its own.
    const heading = await screen.findByRole("heading", { level: 2, name: (name) => name.includes(term(run, "expert_meeting")) });
    expect(heading).toHaveTextContent(term(run, "expert_meeting"));
    const kind = screen.getByLabelText(k("meeting.type"));
    expect(within(kind).getByRole("option", { name: MEETING_TYPE.label })).toBeInTheDocument();
    await user.type(screen.getByLabelText(k("meeting.discussion_on")), "2027-02-12");
    await user.type(screen.getByLabelText(k("meeting.other_expert")), "Dr R");
    expect(await axeViolations(container)).toEqual([]);
    await user.click(screen.getByRole("button", { name: k("meeting.setup") }));
    await waitFor(() =>
      expect(sent.at(-1)).toMatchObject({
        method: "POST", path: `/v1/cases/${CASE}/expert-meetings`,
        body: { type: MEETING_TYPE.key, discussion_on: "2027-02-12", participants: [{ name: "Dr R" }] },
      }),
    );
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
  });

  test("shows the questions in a table with both positions, the deadlines and the pack's declarations", async () => {
    serveMeeting(run);
    const { container } = renderAt(url);
    const title = k("meeting.heading").replace("{{label}}", MEETING_TYPE.label).replace("{{names}}", "Dr R");
    expect(await screen.findByRole("heading", { level: 2, name: title })).toBeInTheDocument();
    expect(screen.getByText(/19 Feb\b/)).toBeInTheDocument();
    expect(screen.getByText(/26 Feb\b/)).toBeInTheDocument();
    const table = screen.getByRole("table");
    for (const name of [k("meeting.col.question"), k("meeting.col.yours"), k("meeting.col.theirs").replace("{{names}}", "Dr R"), k("meeting.item.pages"), k("meeting.item.agreed")]) {
      expect(within(table).getByRole("columnheader", { name })).toBeInTheDocument();
    }
    const first = await rowOf(1);
    expect(within(first).getByText(item(1).own_position!, { exact: false })).toBeInTheDocument();
    expect(within(first).getByText(item(1).other_positions!)).toBeInTheDocument();
    expect(within(first).getByText(k("meeting.item.yes"))).toBeInTheDocument();
    expect(within(first).queryByRole("textbox")).not.toBeInTheDocument();
    const declarations = screen.getByRole("region", { name: k("meeting.declarations.heading") });
    for (const line of MEETING_TYPE.declarations) expect(within(declarations).getByText(line)).toBeInTheDocument();
    expect(await axeViolations(container)).toEqual([]);
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
  });
});

test("a position that differs from the served report asks for a note, sent with the item's version", async () => {
  const { sent, record } = serveMeeting("launch");
  server.use(
    http.patch("/v1/cases/:caseId/agenda-items/:itemId", async ({ request }) => (await record(request), json(item(3, { change_note: "New evidence", version: 6 })))),
  );
  const { user } = renderAt(url);
  const row = await rowOf(3);
  expect(within(row).getByText(k("meeting.item.changed_lead"))).toBeInTheDocument();
  await user.click(within(row).getByRole("button", { name: k("meeting.item.add_note") }));
  const third = await editor(3);
  expect(within(third).getByLabelText(k("meeting.item.change_note"))).toHaveFocus();
  await user.type(within(third).getByLabelText(k("meeting.item.change_note")), "New evidence");
  await user.click(within(third).getByRole("button", { name: k("meeting.item.save") }));
  await waitFor(() =>
    expect(sent.at(-1)).toMatchObject({ method: "PATCH", path: `/v1/cases/${CASE}/agenda-items/${ITEMS[2]}`, ifMatch: '"5"', body: { change_note: "New evidence" } }),
  );
  expect(await within(third).findByText(k("meeting.item.saved"))).toBeInTheDocument();
});

test("a question not agreed takes a reason", async () => {
  const { sent, record } = serveMeeting("launch");
  server.use(http.patch("/v1/cases/:caseId/agenda-items/:itemId", async ({ request }) => (await record(request), json(item(1, { agreed: false, reason: "We differ", version: 4 })))));
  const { user } = renderAt(url);
  await user.click(within(await rowOf(1)).getByRole("button", { name: k("meeting.item.change") }));
  const first = await editor(1);
  expect(within(first).getByLabelText(k("meeting.item.yours"))).toHaveValue(item(1).own_position);
  expect(within(first).getByLabelText(k("meeting.item.theirs"))).toHaveValue(item(1).other_positions);
  await user.click(within(first).getByRole("radio", { name: k("meeting.item.no") }));
  await user.type(within(first).getByLabelText(k("meeting.item.reason")), "We differ");
  await user.click(within(first).getByRole("button", { name: k("meeting.item.save") }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ ifMatch: '"3"', body: { agreed: false, reason: "We differ" } }));
});

test("preparing asks for missing notes, then offers the statement for signing behind the code", async () => {
  const { sent, record } = serveMeeting("launch");
  let prepared = false;
  server.use(
    http.post("/v1/cases/:caseId/expert-meetings/:meetingId/prepare", async ({ request }) => {
      await record(request);
      if (!prepared) {
        prepared = true;
        return apiError(409, "change_note_needed", { items: [ITEMS[2]] });
      }
      return json({ sha256: "e".repeat(64), pages: 4 });
    }),
    http.post("/v1/cases/:caseId/expert-meetings/:meetingId/send-for-signature", async ({ request }) => (await record(request), blobReply("application/pdf"))),
  );
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: k("meeting.prepare") }));
  expect(await screen.findByText(k("meeting.error.change_note_needed"))).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: k("meeting.prepare") }));
  expect(await screen.findByText(k("meeting.prepared").replace("{{pages}}", "4"))).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: k("meeting.download_for_signing") }));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "POST", path: `/v1/cases/${CASE}/expert-meetings/${MEETING}/send-for-signature` }));
  await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled());
});

test("the agenda is sent as a PDF and the screen waits while it is read", async () => {
  const { sent, record } = serveMeeting("launch", { meeting: meetingOut({ items: [], agenda_versions: [] }) });
  server.use(
    http.post("/v1/cases/:caseId/expert-meetings/:meetingId/agenda", async ({ request }) => (await record(request), json({ job_id: "a9999999-9999-4999-8999-999999999999", agenda_version_id: "a9777777-7777-4777-8777-777777777778" }, 202))),
  );
  const { user } = renderAt(url);
  expect(await screen.findByText(k("meeting.agenda.none"))).toBeInTheDocument();
  await user.upload(screen.getByLabelText(k("meeting.agenda.add")), pdf("agenda.pdf"));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "POST", path: `/v1/cases/${CASE}/expert-meetings/${MEETING}/agenda`, contentType: "application/pdf" }));
  expect(await screen.findByText(k("meeting.agenda.reading"))).toBeInTheDocument();
});

test("a signed statement is shown read-only", async () => {
  serveMeeting("launch", { meeting: meetingOut({ status: "signed", signed_sha256: "f".repeat(64) }) });
  renderAt(url);
  expect(await screen.findByText(k("meeting.signed"))).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: k("meeting.item.save") })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: k("meeting.prepare") })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: k("meeting.item.change") })).not.toBeInTheDocument();
  expect(within(await rowOf(1)).getByText(item(1).own_position!, { exact: false })).toBeInTheDocument();
});

test("a signed copy made elsewhere is added as a PDF", async () => {
  const { sent, record } = serveMeeting("launch");
  server.use(http.post("/v1/cases/:caseId/expert-meetings/:meetingId/signed-external", async ({ request }) => (await record(request), json(meetingOut({ status: "signed_external" })))));
  const { user } = renderAt(url);
  await user.upload(await screen.findByLabelText(k("meeting.add_signed")), pdf("signed.pdf"));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ path: `/v1/cases/${CASE}/expert-meetings/${MEETING}/signed-external`, contentType: "application/pdf" }));
});

describe("the screen as designed", () => {
  test("a link back to after serving, who it is with, the agenda version and when it arrived", async () => {
    serveMeeting("launch");
    renderAt(url);
    const back = await screen.findByRole("link", { name: k("meeting.back") });
    expect(back).toHaveAttribute("href", expect.stringContaining(`/cases/${CASE}`));
    const line = await screen.findByTestId("meeting-line");
    expect(line).toHaveTextContent("Dr R");
    expect(line).toHaveTextContent("10 February 2027");
    expect(line).toHaveTextContent("2");
  });

  test("each deadline is a chip with the rule it comes from", async () => {
    serveMeeting("launch");
    renderAt(url);
    const sign = await screen.findByText(k("meeting.sign_by"));
    expect(sign.closest("li")).toHaveTextContent("Rule Z.9");
    expect(sign.closest("li")).toHaveTextContent("19 Feb");
    expect(screen.getByText(k("meeting.send_by")).closest("li")).toHaveTextContent("Rule Z.10");
  });

  test("each question shows the pages it cites as page references", async () => {
    serveMeeting("launch");
    renderAt(url);
    const second = await rowOf(2);
    expect(within(second).getByText("p.214")).toBeInTheDocument();
    expect(within(second).getByText("p.87")).toBeInTheDocument();
  });

  test("a changed position is marked in its row and its note link opens the editor at the note", async () => {
    serveMeeting("launch");
    const { user } = renderAt(url);
    const row = await rowOf(3);
    expect(row).toHaveClass("av-trial-row--changed");
    await user.click(within(row).getByRole("button", { name: k("meeting.item.add_note") }));
    expect(within(await editor(3)).getByLabelText(k("meeting.item.change_note"))).toHaveFocus();
  });

  test("each question shows the paragraph of the report it comes from", async () => {
    serveMeeting("launch");
    renderAt(url);
    expect(within(await rowOf(1)).getByText(k("meeting.item.para").replace("{{paras}}", "13"))).toBeInTheDocument();
  });

  test("a question not agreed shows its reason in the row", async () => {
    serveMeeting("launch");
    renderAt(url);
    expect(within(await rowOf(2)).getByText(k("meeting.item.reason_shown").replace("{{reason}}", item(2).reason!))).toBeInTheDocument();
  });

  test("the pack's declarations are shown ticked and cannot be unticked", async () => {
    serveMeeting("launch");
    renderAt(url);
    const declarations = await screen.findByRole("region", { name: k("meeting.declarations.heading") });
    for (const line of MEETING_TYPE.declarations) {
      const box = within(declarations).getByRole("checkbox", { name: line });
      expect(box).toBeChecked();
      expect(box).toBeDisabled();
    }
    expect(within(declarations).getByText(k("meeting.footnote"))).toBeInTheDocument();
  });

  test("another expert is added by name and the meeting then lists them", async () => {
    const { sent, record } = serveMeeting("launch");
    server.use(
      http.post("/v1/cases/:caseId/expert-meetings/:meetingId/participants", async ({ request }) => (await record(request), json({ id: "a9888888-8888-4888-8888-888888888889", name: "Dr S", discipline: null, side: null, on_avodeck: false }, 201))),
    );
    const { user } = renderAt(url);
    await user.click(await screen.findByRole("button", { name: k("meeting.add_expert") }));
    await user.type(screen.getByLabelText(k("meeting.other_expert")), "Dr S");
    await user.click(screen.getByRole("button", { name: k("meeting.add_expert_save") }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ method: "POST", path: `/v1/cases/${CASE}/expert-meetings/${MEETING}/participants`, body: { name: "Dr S" } }));
  });

  test("the prepare button is the screen's one main action", async () => {
    serveMeeting("launch");
    renderAt(url);
    const prepare = await screen.findByRole("button", { name: k("meeting.prepare") });
    expect(prepare.className).not.toMatch(/secondary/);
  });
});

test("it sits in the case frame: the case bar with the case name as the page's main heading", async () => {
  serveMeeting("launch");
  renderAt(url);
  expect(await screen.findByRole("heading", { level: 1, name: "Mrs A" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: M["trial.meeting.back"]! })).toBeInTheDocument();
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
});
