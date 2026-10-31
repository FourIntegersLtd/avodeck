// Every action on screens 14 and 15 shows it is working: the control is disabled and busy while the request runs,
// and a second click sends nothing more.
import { screen, waitFor, within } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";
import { expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { claim, FACT_2, lintIssue, ok, opinionOut, pointFact, pointOut, pointsBody, Q1, serve, sides, workspace } from "./testing";
import { WRITE_MESSAGES as M } from "./words";

const points = `/cases/${CASE_A}/points?q=${Q1}`;
const opinion = `/cases/${CASE_A}/opinion`;

/** A handler that counts its hits and stays open until `release()`. */
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

async function expectBusyOnce(user: UserEvent, button: HTMLElement, hits: { n: number }) {
  await user.click(button);
  await waitFor(() => expect(button).toBeDisabled());
  expect(button).toHaveAttribute("aria-busy", "true");
  await user.click(button);
  expect(hits.n).toBe(1);
}

test("keeping a point is busy and sends once", async () => {
  serve("launch");
  const h = hold(() => ok(pointOut({ status: "accepted", version: 2 }), 2));
  server.use(http.patch("/v1/cases/:caseId/points/:id", h.handler));
  const { user } = renderAt(points);
  await expectBusyOnce(user, await screen.findByRole("button", { name: M["write.points.keep"]! }), h.hits);
  h.release();
});

test("adding an unused fact is busy and sends once", async () => {
  serve("launch");
  const h = hold(() => json(pointOut({ id: "b2222222-2222-4222-8222-222222222222", fact: pointFact({ id: FACT_2 }) }), 201));
  server.use(http.post("/v1/cases/:caseId/questions/:q/points", h.handler));
  const { user } = renderAt(points);
  await user.click((await screen.findAllByRole("button", { name: M["write.points.add_row_button"]! }))[1]!);
  const unused = await screen.findByRole("region", { name: M["write.points.unused"] });
  await expectBusyOnce(user, within(unused).getByRole("button", { name: `Add to ${sides("launch")[1]!.label}` }), h.hits);
  h.release();
});

test("dismissing a relevant fact is busy and sends once", async () => {
  const relevant = { id: "d2222222-2222-4222-8222-222222222222", status: "suggested", version: 3, why_relevant: "mentions pain", fact: pointFact({ id: FACT_2, value: "Out-of-hours call" }) };
  serve("launch", { points: pointsBody("launch", { relevant: [relevant] }) });
  const h = hold(() => ok({ ...relevant, status: "dismissed", version: 4 }, 4));
  server.use(http.patch("/v1/cases/:caseId/relevant-facts/:id", h.handler));
  const { user } = renderAt(points);
  await expectBusyOnce(user, await screen.findByRole("button", { name: M["write.points.relevant_dismiss"]! }), h.hits);
  h.release();
});

test("deciding on an account that differs is busy and sends once", async () => {
  serve("launch", { points: pointsBody("launch", { claims: [claim()] }) });
  const h = hold(() => ok(claim({ status: "confirmed", version: 2 }), 2));
  server.use(http.patch("/v1/cases/:caseId/witness-claims/:id", h.handler));
  const { user } = renderAt(points);
  await expectBusyOnce(user, await screen.findByRole("button", { name: M["write.points.claim_confirm"]! }), h.hits);
  h.release();
});

test("saving the range note is busy and sends once", async () => {
  serve("launch");
  const h = hold(() => ok({ text: "x", version: 1, dictated_on: null }, 1));
  server.use(http.put("/v1/cases/:caseId/questions/:q/range-note", h.handler));
  const { user } = renderAt(points);
  await expectBusyOnce(user, await screen.findByRole("button", { name: M["write.points.range_save"]! }), h.hits);
  h.release();
});

test("removing a point is busy in the dialog and sends once", async () => {
  serve("launch");
  const h = hold(() => ok(pointOut({ status: "dismissed", version: 2 }), 2));
  server.use(http.patch("/v1/cases/:caseId/points/:id", h.handler));
  const { user } = renderAt(points);
  await user.click(await screen.findByRole("button", { name: M["write.points.more"]! }));
  await user.click(await screen.findByRole("button", { name: M["write.points.remove"]! }));
  const dialog = await screen.findByRole("dialog", { name: M["write.points.remove_title"] });
  await user.type(within(dialog).getByLabelText(M["write.points.remove_reason"]!), "Not relevant");
  await expectBusyOnce(user, within(dialog).getByRole("button", { name: M["write.points.remove_confirm"]! }), h.hits);
  h.release();
});

test("restoring a version is busy and sends once", async () => {
  serve("launch", { workspace: workspace("launch", { opinion: opinionOut({ body: "Now", version: 3 }) }) });
  const version = (n: number, body: string) => ({ version_no: n, body, citation_refs: [], view: null, provisional: false, restored_from: null, saved_by: null, saved_at: `2026-10-1${n}T09:00:00Z` });
  const h = hold(() => ok(opinionOut({ body: "Earlier words", version: 4, restored_from: 2 }), 4));
  server.use(
    http.get("/v1/cases/:caseId/opinions/:id/versions", () => json({ versions: [version(3, "Now"), version(2, "Earlier words")] })),
    http.post("/v1/cases/:caseId/opinions/:id/versions/:n/restore", h.handler),
  );
  const { user } = renderAt(opinion);
  await user.click(await screen.findByRole("button", { name: M["write.opinion.versions"]! }));
  const list = await screen.findByRole("region", { name: M["write.opinion.versions"] });
  await expectBusyOnce(user, within(list).getByRole("button", { name: "Go back to version 2" }), h.hits);
  h.release();
});

test("dismissing a check is busy in the dialog and sends once", async () => {
  const issue = lintIssue();
  serve("launch", { workspace: workspace("launch", { opinion: opinionOut({ body: "x", lint: [issue] }) }) });
  const h = hold(() => json({ ...issue, status: "dismissed" }));
  server.use(http.patch("/v1/cases/:caseId/lint-issues/:id", h.handler));
  const { user } = renderAt(opinion);
  const panel = await screen.findByRole("region", { name: M["write.opinion.checks"] });
  await user.click(within(panel).getByRole("button", { name: M["write.opinion.issue_dismiss"]! }));
  const dialog = await screen.findByRole("dialog", { name: M["write.opinion.dismiss_title"] });
  await user.type(within(dialog).getByLabelText(M["write.opinion.dismiss_reason"]!), "My note");
  await expectBusyOnce(user, within(dialog).getByRole("button", { name: M["write.opinion.dismiss_confirm"]! }), h.hits);
  h.release();
});

test("moving a point to the other side says so, and a second move sends nothing", async () => {
  serve("launch");
  const h = hold(() => ok(pointOut({ side: sides("launch")[1]!.code, version: 2 }), 2));
  server.use(http.patch("/v1/cases/:caseId/points/:id", h.handler));
  const { user } = renderAt(points);
  await user.click(await screen.findByRole("button", { name: M["write.points.more"]! }));
  await user.click(await screen.findByRole("button", { name: `Move to ${sides("launch")[1]!.label}` }));
  expect(await screen.findByText("Moving the point…")).toBeInTheDocument();
  expect(document.querySelector("#point-b1111111-1111-4111-8111-111111111111")).toHaveAttribute("aria-busy", "true");
  await user.click(screen.getByRole("button", { name: M["write.points.more"]! }));
  const again = screen.queryByRole("button", { name: `Move to ${sides("launch")[1]!.label}` });
  if (again) await user.click(again);
  expect(h.hits.n).toBe(1);
  h.release();
  await waitFor(() => expect(screen.queryByText("Moving the point…")).not.toBeInTheDocument());
});
