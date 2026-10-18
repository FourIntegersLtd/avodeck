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
    const heading = await screen.findByRole("heading", { level: 1, name: (name) => name.includes(term(run, "expert_meeting")) });
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

  test("shows the questions with both positions, the deadlines and the pack's declarations", async () => {
    serveMeeting(run);
    const { container } = renderAt(url);
    const title = k("meeting.heading").replace("{{label}}", MEETING_TYPE.label).replace("{{names}}", "Dr R");
    expect(await screen.findByRole("heading", { level: 1, name: title })).toBeInTheDocument();
    expect(screen.getByText(/19 February 2027/)).toBeInTheDocument();
    expect(screen.getByText(/26 February 2027/)).toBeInTheDocument();
    const first = screen.getByRole("group", { name: `${k("meeting.item.question").replace("{{number}}", "1")}: ${item(1).text}` });
    expect(within(first).getByLabelText(k("meeting.item.yours"))).toHaveValue(item(1).own_position);
    expect(within(first).getByLabelText(k("meeting.item.theirs"))).toHaveValue(item(1).other_positions);
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
  const third = await screen.findByRole("group", { name: `${k("meeting.item.question").replace("{{number}}", "3")}: ${item(3).text}` });
  expect(within(third).getByText(k("meeting.item.changed"))).toBeInTheDocument();
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
  const first = await screen.findByRole("group", { name: `${k("meeting.item.question").replace("{{number}}", "1")}: ${item(1).text}` });
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
  expect(screen.getAllByText(item(1).own_position!)[0]).toBeInTheDocument();
});

test("a signed copy made elsewhere is added as a PDF", async () => {
  const { sent, record } = serveMeeting("launch");
  server.use(http.post("/v1/cases/:caseId/expert-meetings/:meetingId/signed-external", async ({ request }) => (await record(request), json(meetingOut({ status: "signed_external" })))));
  const { user } = renderAt(url);
  await user.upload(await screen.findByLabelText(k("meeting.add_signed")), pdf("signed.pdf"));
  await waitFor(() => expect(sent.at(-1)).toMatchObject({ path: `/v1/cases/${CASE}/expert-meetings/${MEETING}/signed-external`, contentType: "application/pdf" }));
});
