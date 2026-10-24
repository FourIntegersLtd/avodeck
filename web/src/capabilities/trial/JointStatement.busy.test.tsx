// S22: every action shows it is working and ignores a second click or file.
import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, test, vi } from "vitest";
import { http, json, server } from "../../test/api";
import { renderAt } from "../../test/render";
import { expectBusyOnce, expectUploadBusyOnce, hold } from "./busyTesting";
import { blobReply, CASE, item, meetingOut, serveMeeting } from "./testing";
import { TRIAL_MESSAGES as M } from "./words";

const url = `/cases/${CASE}/joint-statement`;
const k = (key: string) => M[`trial.${key}`]!;

beforeEach(() => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:statement"), revokeObjectURL: vi.fn() }));
});
afterEach(() => vi.unstubAllGlobals());

test("adding the agenda shows it is sending and takes no second file", async () => {
  serveMeeting("launch", { meeting: meetingOut({ items: [], agenda_versions: [] }) });
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/expert-meetings/:meetingId/agenda", async () => (await h.enter(), json({ job_id: "a9999999-9999-4999-8999-999999999999", agenda_version_id: "a9777777-7777-4777-8777-777777777778" }, 202))));
  const { user } = renderAt(url);
  try {
    await expectUploadBusyOnce(user, await screen.findByLabelText(k("meeting.agenda.add")), h);
  } finally { h.release(); }
});

test("adding a signed copy shows it is sending and takes no second file", async () => {
  serveMeeting("launch");
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/expert-meetings/:meetingId/signed-external", async () => (await h.enter(), json(meetingOut({ status: "signed_external" })))));
  const { user } = renderAt(url);
  try {
    await expectUploadBusyOnce(user, await screen.findByLabelText(k("meeting.add_signed")), h);
  } finally { h.release(); }
});

test("saving a position goes busy and sends once", async () => {
  serveMeeting("launch");
  const h = hold();
  server.use(http.patch("/v1/cases/:caseId/agenda-items/:itemId", async () => (await h.enter(), json(item(1, { version: 4 })))));
  const { user } = renderAt(url);
  const row = await screen.findByRole("row", { name: (name) => name.includes(`1. ${item(1).text}`) });
  await user.click(within(row).getByRole("button", { name: k("meeting.item.change") }));
  try {
    await expectBusyOnce(user, await screen.findByRole("button", { name: k("meeting.item.save") }), h);
  } finally { h.release(); }
});

test("adding another expert goes busy and sends once", async () => {
  serveMeeting("launch");
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/expert-meetings/:meetingId/participants", async () => (await h.enter(), json({ id: "x", name: "Dr Lee" }, 201))));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: k("meeting.add_expert") }));
  await user.type(await screen.findByLabelText(k("meeting.other_expert")), "Dr Lee");
  try {
    await expectBusyOnce(user, screen.getByRole("button", { name: k("meeting.add_expert_save") }), h);
  } finally { h.release(); }
});

test("downloading the statement for signing goes busy and sends once", async () => {
  serveMeeting("launch");
  const h = hold();
  server.use(
    http.post("/v1/cases/:caseId/expert-meetings/:meetingId/prepare", () => json({ pages: 4 })),
    http.post("/v1/cases/:caseId/expert-meetings/:meetingId/send-for-signature", async () => (await h.enter(), blobReply("application/pdf"))),
  );
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: k("meeting.prepare") }));
  try {
    await expectBusyOnce(user, await screen.findByRole("button", { name: k("meeting.download_for_signing") }), h);
  } finally { h.release(); }
});
