// S24: every action shows it is working and ignores a second click.
import { screen } from "@testing-library/react";
import { afterEach, beforeEach, test, vi } from "vitest";
import { http, json, server } from "../../test/api";
import { renderAt } from "../../test/render";
import { expectBusyOnce, hold } from "./busyTesting";
import { memoryStore, setCopyStore } from "./offline";
import { blobReply, CASE, etagged, servePack, settingsOut } from "./testing";
import { TRIAL_MESSAGES as M } from "./words";

const url = `/cases/${CASE}/trial-pack`;
const k = (key: string) => M[`trial.${key}`]!;

beforeEach(() => {
  setCopyStore(memoryStore());
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:pack"), revokeObjectURL: vi.fn() }));
});
afterEach(() => vi.unstubAllGlobals());

test("saving what goes in goes busy and sends once", async () => {
  servePack("launch");
  const h = hold();
  server.use(http.put("/v1/cases/:caseId/trial-pack/settings", async () => (await h.enter(), etagged(settingsOut({ version: 3 }), 3))));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("checkbox", { name: k("pack.section.written_answers") }));
  try {
    await expectBusyOnce(user, screen.getByRole("button", { name: k("pack.save") }), h);
  } finally { h.release(); }
});

test("downloading the pack goes busy and sends once", async () => {
  servePack("launch");
  const h = hold();
  server.use(http.get("/v1/cases/:caseId/trial-pack/latest/download", async () => (await h.enter(), blobReply("application/pdf"))));
  const { user } = renderAt(url);
  try {
    await expectBusyOnce(user, await screen.findByRole("button", { name: k("pack.download") }), h);
  } finally { h.release(); }
});

test("checking a new note goes busy and sends once", async () => {
  servePack("launch");
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/trial-pack/check-new", async () => (await h.enter(), json({ sentences: [] }))));
  const { user } = renderAt(url);
  await user.click(await screen.findByRole("button", { name: k("pack.check.open") }));
  await user.type(await screen.findByLabelText(k("pack.check.label")), "A note.");
  try {
    await expectBusyOnce(user, screen.getByRole("button", { name: k("pack.check.run") }), h);
  } finally { h.release(); }
});

test("making the pack goes busy at once and sends once", async () => {
  servePack("launch", { latest: { pack: null, settings: settingsOut() } as never });
  const h = hold();
  server.use(http.post("/v1/cases/:caseId/trial-pack", async () => (await h.enter(), json({ job_id: "a9999999-9999-4999-8999-999999999999" }, 202))));
  const { user } = renderAt(url);
  try {
    await expectBusyOnce(user, await screen.findByRole("button", { name: k("pack.build") }), h);
  } finally { h.release(); }
});
