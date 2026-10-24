// The undo on the set-aside pages and the "more time" tick on the request email show they are working.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { launchPack } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { CASE_ID, draft, EXPERT_ACTIONS, pageId, summary, THUMB, theCase } from "./testData";

function hold(respond: () => Response) {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const hits = { n: 0 };
  const handler = async () => {
    hits.n += 1;
    try {
      await gate;
    } finally {
      /* released by the test */
    }
    return respond();
  };
  return { hits, release, handler };
}

test("undoing a set-aside page is busy and sends once", async () => {
  signedIn({ pack: launchPack });
  const page = { page_id: pageId(415), page_no: 415, status: "confirmed" as const, reason: "identifiers_differ" as const, job_id: null };
  const h = hold(() => json({ ...page, status: "undone" }));
  server.use(
    http.get(`/v1/cases/${CASE_ID}`, () => json(theCase({ allowed_actions: EXPERT_ACTIONS }))),
    http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () => json(summary(launchPack, { quarantine_suggestions: [], quarantined: [page] }))),
    http.get(`/v1/cases/${CASE_ID}/pages/:pageNo/thumbnail`, () => new HttpResponse(THUMB, { headers: { "Content-Type": "image/jpeg" } })),
    http.delete(`/v1/cases/${CASE_ID}/pages/${pageId(415)}/quarantine`, h.handler),
  );
  const { user } = renderAt(`/cases/${CASE_ID}/bundle/not-this-patient`);
  const button = await screen.findByRole("button", { name: "Undo" });
  await user.click(button);
  await waitFor(() => expect(button).toBeDisabled());
  expect(button).toHaveAttribute("aria-busy", "true");
  await user.click(button);
  expect(h.hits.n).toBe(1);
  h.release();
});

test("ticking more time is busy, says the email is updating, and asks once", async () => {
  signedIn({ pack: launchPack });
  const extension = { days: 14, due_date: "2026-11-30", after_date: "2026-11-16" };
  let first = true;
  const h = hold(() => json(draft({ extension, body: "Body with the extra line" })));
  server.use(
    http.get(`/v1/cases/${CASE_ID}`, () => json(theCase())),
    http.get(`/v1/cases/${CASE_ID}/bundle/summary`, () => json(summary(launchPack))),
    http.post(`/v1/cases/${CASE_ID}/instructing-party-requests/draft`, async () => {
      if (first) {
        first = false;
        return json(draft({ extension, body: "Body without it" }));
      }
      return h.handler();
    }),
  );
  const { user } = renderAt(`/cases/${CASE_ID}/bundle/ask`);
  expect(await screen.findByText("Body without it")).toBeVisible();
  const box = screen.getByRole("checkbox", { name: /Add a line asking for 14 more days/ });
  await user.click(box);
  await waitFor(() => expect(box).toBeDisabled());
  expect(box).toHaveAttribute("aria-busy", "true");
  expect(within(screen.getByRole("status")).getByText("Updating the email…")).toBeVisible();
  expect(h.hits.n).toBe(1);
  h.release();
  expect(await screen.findByText("Body with the extra line")).toBeVisible();
});
