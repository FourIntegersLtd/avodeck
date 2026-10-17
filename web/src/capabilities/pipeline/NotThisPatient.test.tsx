// Screen 30, not this patient's record: pages the pipeline thinks belong to someone else. The named
// expert decides each one; set-aside pages can be undone until they are deleted. Both pack sets.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { apiError, http, HttpResponse, json, server, signedIn } from "../../test/api";
import { fixturePack, launchPack } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { CASE_ID, draft, EXPERT_ACTIONS, packWords, pageId, summary, THUMB, theCase } from "./testData";

const PATH = `/cases/${CASE_ID}/bundle/not-this-patient`;

type Q = Schemas["QuarantinePage"];
const suggested = (n: number): Q => ({ page_id: pageId(n), page_no: n, status: "suggested", reason: "identifiers_differ", job_id: null });

function mock(pack: Schemas["CasePack"], opts: { suggestions?: Q[]; quarantined?: Q[]; actions?: string[] } = {}) {
  signedIn({ pack });
  const party = packWords(pack).party;
  server.use(
    http.get(`/v1/cases/${CASE_ID}`, () => json(theCase({ instructing_party_type: party.key, allowed_actions: opts.actions ?? EXPERT_ACTIONS }))),
    http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () =>
      json(summary(pack, { quarantine_suggestions: opts.suggestions ?? [412, 413, 414].map(suggested), quarantined: opts.quarantined ?? [] })),
    ),
    http.get(`/v1/cases/${CASE_ID}/pages/:pageNo/thumbnail`, () => new HttpResponse(THUMB, { headers: { "Content-Type": "image/jpeg" } })),
  );
  return party;
}

const pageCard = (n: number) => screen.getByRole("heading", { name: `p.${n}` }).closest("li") as HTMLElement;

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("Not this patient under the $name", ({ pack }) => {
  test("says how many days pass before set-aside pages are deleted, from the pack", async () => {
    mock(pack);
    renderAt(PATH);
    const days = pack.quarantine_purge_days;
    await screen.findByRole("heading", { level: 1, name: "These pages may belong to someone else" });
    expect(screen.getByText(`After the note is sent, or after ${days} ${days === 1 ? "day" : "days"}. You can undo until then.`)).toBeVisible();
  });

  test("lists each page with its picture and why, and explains what setting aside does", async () => {
    const party = mock(pack);
    const { container } = renderAt(PATH);
    expect(await screen.findByRole("heading", { level: 1, name: "These pages may belong to someone else" })).toBeVisible();
    expect(screen.getByText("The name or date of birth on them doesn't match Mrs A. Check each one. Nothing is hidden until you say so.")).toBeVisible();
    const first = pageCard(412);
    expect(within(first).getByText("Different name or date of birth")).toBeVisible();
    expect(within(first).getByRole("img", { name: "Page 412" })).toBeInTheDocument();
    expect(within(first).getByRole("button", { name: "Not Mrs A's" })).toHaveAttribute("aria-pressed", "false");
    expect(within(first).getByRole("button", { name: "It is Mrs A's" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "What happens when you set them aside" })).toBeVisible();
    expect(screen.getByText(`The ${party.label} is told.`)).toBeVisible();
    expect(screen.getByText("Page numbers stay the same.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Decide later" })).toHaveAttribute("href", `/cases/${CASE_ID}/bundle`);
    expect(screen.getByRole("button", { name: "Set aside 0 pages and draft the note" })).toBeDisabled();
    expect(await axeViolations(container)).toEqual([]);
  });

  test("setting pages aside confirms them, clears the rest, then drafts the note", async () => {
    mock(pack);
    const posted: Array<{ id: string; body: unknown }> = [];
    const deleted: string[] = [];
    server.use(
      http.post(`/v1/cases/${CASE_ID}/pages/:pageId/quarantine`, async ({ params, request }) => {
        posted.push({ id: String(params.pageId), body: await request.json() });
        return json({ ...suggested(412), page_id: String(params.pageId), status: "confirmed" });
      }),
      http.delete(`/v1/cases/${CASE_ID}/pages/:pageId/quarantine`, ({ params }) => {
        deleted.push(String(params.pageId));
        return json({ ...suggested(414), status: "dismissed" });
      }),
      http.post(`/v1/cases/${CASE_ID}/instructing-party-requests/draft`, () => json(draft({ label: packWords(pack).askLabel }))),
    );
    const { user, router } = renderAt(PATH);
    await user.click(within(await screen.findByRole("heading", { name: "p.412" }).then((h) => h.closest("li")!)).getByRole("button", { name: "Not Mrs A's" }));
    await user.click(within(pageCard(413)).getByRole("button", { name: "Not Mrs A's" }));
    await user.click(within(pageCard(414)).getByRole("button", { name: "It is Mrs A's" }));
    expect(within(pageCard(412)).getByRole("button", { name: "Not Mrs A's" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Set aside 2 pages and draft the note" }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/cases/${CASE_ID}/bundle/ask`));
    expect(posted.map((p) => p.id).sort()).toEqual([pageId(412), pageId(413)]);
    expect(posted[0]!.body).toEqual({ reason: "identifiers_differ" });
    expect(deleted).toEqual([pageId(414)]);
  });

  test("saying every page is the patient's saves the choices and goes back to the bundle", async () => {
    mock(pack, { suggestions: [suggested(412)] });
    server.use(http.delete(`/v1/cases/${CASE_ID}/pages/:pageId/quarantine`, () => json({ ...suggested(412), status: "dismissed" })));
    const { user, router } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "It is Mrs A's" }));
    await user.click(screen.getByRole("button", { name: "Save my choices" }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/cases/${CASE_ID}/bundle`));
  });

  test("a page already set aside can be undone", async () => {
    mock(pack, { suggestions: [], quarantined: [{ ...suggested(415), status: "confirmed" }] });
    server.use(http.delete(`/v1/cases/${CASE_ID}/pages/${pageId(415)}/quarantine`, () => json({ ...suggested(415), status: "undone" })));
    const { user } = renderAt(PATH);
    const card = await screen.findByRole("heading", { name: "p.415" }).then((h) => h.closest("li")!);
    expect(within(card).getByText("Set aside")).toBeVisible();
    await user.click(within(card).getByRole("button", { name: "Undo" }));
    expect(await within(pageCard(415)).findByText("Back in the bundle.")).toBeVisible();
  });

  test("a page that has already been deleted cannot be brought back, and it says so", async () => {
    mock(pack, { suggestions: [], quarantined: [{ ...suggested(415), status: "confirmed" }] });
    server.use(http.delete(`/v1/cases/${CASE_ID}/pages/${pageId(415)}/quarantine`, () => apiError(409, "conflict")));
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Undo" }));
    expect(await screen.findByText("This page has been deleted, so it can't be brought back.")).toBeVisible();
  });

  test("only the named expert decides; others see the pages without the choices", async () => {
    mock(pack, { actions: ["case.view_metadata", "case.view_content", "page.quarantine"] });
    renderAt(PATH);
    expect(await screen.findByText("Only the named expert can decide about these pages.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Not Mrs A's" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Set aside/ })).toBeNull();
  });

  test("with nothing to check it says so", async () => {
    mock(pack, { suggestions: [] });
    renderAt(PATH);
    expect(await screen.findByText("No pages are waiting to be checked.")).toBeVisible();
  });
});
