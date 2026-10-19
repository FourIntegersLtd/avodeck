// Screen 08b, ask the instructing party for what's missing: the API drafts the email from the pack's
// wording; the expert edits it, it is noted in the case record, and their own email opens with it.
import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { apiError, http, json, server, signedIn } from "../../test/api";
import { fixturePack, launchPack } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { mail } from "./mail";
import { CASE_ID, draft, packWords, pageId, summary, theCase } from "./testData";

const PATH = `/cases/${CASE_ID}/bundle/ask`;
const MAILTO = "mailto:?subject=Records&body=Please";

afterEach(() => vi.restoreAllMocks());

function mock(pack: Schemas["CasePack"]) {
  signedIn({ pack });
  const label = packWords(pack).askLabel;
  server.use(
    http.get(`/v1/cases/${CASE_ID}`, () => json(theCase())),
    http.post(`/v1/cases/${CASE_ID}/instructing-party-requests/draft`, () => json(draft({ label }))),
    http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () =>
      json(
        summary(pack, {
          quarantined: [
            { page_id: pageId(412), page_no: 412, status: "confirmed", reason: "identifiers_differ", job_id: null },
            { page_id: pageId(413), page_no: 413, status: "undone", reason: "identifiers_differ", job_id: null },
          ],
        }),
      ),
    ),
  );
  return label;
}

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("Ask the instructing party under the $name", ({ pack }) => {
  test("shows the drafted email under the pack's words, and says patient details are left out", async () => {
    const label = mock(pack);
    const { container } = renderAt(PATH);
    expect(await screen.findByRole("heading", { level: 1, name: `${label} for what's missing` })).toBeVisible();
    expect(screen.getByText("We've written it for you. Change anything you like, then send it from your own email.")).toBeVisible();
    expect(screen.getByText("Records needed - Mrs A")).toBeVisible();
    expect(screen.getByText(/Colonoscopy report from September/)).toBeVisible();
    expect(screen.getByText("Patient details are left out. Page numbers are from your bundle.")).toBeVisible();
    expect(screen.getByRole("heading", { name: "What happens next" })).toBeVisible();
    expect(screen.getByText("When the records arrive, add them to the case. They're numbered after page 1,231, so nothing you've already read moves.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Your bundle" })).toHaveAttribute("href", `/cases/${CASE_ID}/bundle`);
    expect(await axeViolations(container)).toEqual([]);
  });

  test("sending notes the request with what it asks for, then opens the expert's own email", async () => {
    mock(pack);
    let sent: Record<string, unknown> | null = null;
    server.use(
      http.post(`/v1/cases/${CASE_ID}/instructing-party-requests`, async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>;
        return json({ id: "r1", mailto: MAILTO }, 201);
      }),
    );
    const open = vi.spyOn(mail, "open").mockImplementation(() => {});
    const { user, container } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Edit the email" }));
    await user.type(screen.getByLabelText("To"), "records@example.test");
    const subject = screen.getByLabelText("Subject");
    await user.clear(subject);
    await user.type(subject, "Records for Mrs A");
    expect(await axeViolations(container)).toEqual([]);
    await user.click(screen.getByRole("button", { name: "Send from my email" }));
    expect(await screen.findByText("Your email is open, ready to send. We've noted the request in your case record.")).toBeVisible();
    expect(open).toHaveBeenCalledWith(MAILTO);
    expect(sent).toEqual({
      to: "records@example.test",
      subject: "Records for Mrs A",
      body: draft().body,
      missing_item_ids: draft().missing_item_ids,
      clearer_copy_page_ids: draft().clearer_copy_page_ids,
      quarantined_page_ids: [pageId(412)],
    });
  });

  test("an address the API refuses asks the expert to check it", async () => {
    mock(pack);
    server.use(http.post(`/v1/cases/${CASE_ID}/instructing-party-requests`, () => apiError(422, "validation_error")));
    const open = vi.spyOn(mail, "open").mockImplementation(() => {});
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Edit the email" }));
    await user.type(screen.getByLabelText("To"), "not-an-address");
    await user.click(screen.getByRole("button", { name: "Send from my email" }));
    expect(await screen.findByText("Please check the email address and the message.")).toBeVisible();
    expect(open).not.toHaveBeenCalled();
  });

  test("Copy the text puts the subject and message on the clipboard", async () => {
    mock(pack);
    const { user } = renderAt(PATH);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await user.click(await screen.findByRole("button", { name: "Copy the text" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(`Records needed - Mrs A\n\n${draft().body}`));
    expect(await screen.findByText("Copied.")).toBeVisible();
  });

  test("a draft that cannot be made shows the shared error", async () => {
    mock(pack);
    server.use(http.post(`/v1/cases/${CASE_ID}/instructing-party-requests/draft`, () => apiError(500, "server_error")));
    renderAt(PATH);
    expect(await screen.findByRole("button", { name: "Try again" })).toBeVisible();
  });

  test("shows who it is from and the To field without pressing Edit", async () => {
    mock(pack);
    renderAt(PATH);
    expect(await screen.findByText("Dr Jane Morgan \u00b7 your usual email")).toBeVisible();
    expect(screen.getByLabelText("To")).toBeVisible();
    expect(screen.queryByText("Need more time?")).toBeNull();
  });
});
