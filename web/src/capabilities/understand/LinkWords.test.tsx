// Slice D stuck fixes: a link that cannot be confirmed says why in plain words and points to the page to recheck;
// fact kinds on S11, S12 and S13 use the case pack's words.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { apiError, http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { casePack, event, finding, inCase, link, timeline } from "./testing";

function packWithResult(label: string) {
  const pack = casePack("launch");
  return { ...pack, fact_kinds: [{ key: "result", label, field_labels: {} }] };
}

function serve(links = [link()]) {
  server.use(
    http.get("/v1/cases/:caseId/findings", () => json({ findings: [finding()] })),
    http.get("/v1/me/checks", () => json({ checks: [] })),
    http.get("/v1/cases/:caseId/fact-links", () => json({ links })),
    http.get("/v1/cases/:caseId/timeline", () => json(timeline())),
  );
}

test("a link not yet checked says so in plain words and links to the page to recheck", async () => {
  inCase("launch");
  serve();
  server.use(http.patch("/v1/cases/:caseId/fact-links/:id", () => apiError(409, "not_checked")));
  const { user } = renderAt(`/cases/${CASE_A}/things-to-look-at`);
  const links = await screen.findByRole("region", { name: "Links to the questions" });
  await user.click(await within(links).findByRole("button", { name: "Confirm link" }));
  expect(await within(links).findByText("Check this first, then try again.")).toBeInTheDocument();
  const recheck = within(links).getByRole("link", { name: "Check this entry again" });
  expect(recheck).toHaveAttribute("href", `/cases/${CASE_A}/read/214?fact=${link().fact_id}`);
});

test("a link that no longer holds shows the shared stale-link words, never the code", async () => {
  inCase("launch");
  serve();
  server.use(http.patch("/v1/cases/:caseId/fact-links/:id", () => apiError(409, "stale_link")));
  const { user } = renderAt(`/cases/${CASE_A}/things-to-look-at`);
  const links = await screen.findByRole("region", { name: "Links to the questions" });
  await user.click(await within(links).findByRole("button", { name: "Confirm link" }));
  await waitFor(() => expect(within(links).getByRole("alert")).toBeInTheDocument());
  expect(links.textContent).not.toMatch(/stale_link|not_checked/);
});

test("entries in the links use the pack's word for the fact kind", async () => {
  inCase("launch");
  server.use(http.get("/v1/cases/:caseId/pack", () => json(packWithResult("Zz lab result"))));
  serve();
  renderAt(`/cases/${CASE_A}/things-to-look-at`);
  const links = await screen.findByRole("region", { name: "Links to the questions" });
  expect((await within(links).findAllByText("Zz lab result · 19 February 2024 · p.214")).length).toBeGreaterThan(0);
});

test("the timeline list uses the pack's word for the fact kind", async () => {
  inCase("launch");
  server.use(
    http.get("/v1/cases/:caseId/pack", () => json(packWithResult("Zz lab result"))),
    http.get("/v1/cases/:caseId/timeline", () => json(timeline({ lanes: [{ key: "lane_one", label: "Lane one", events: [event({ kind: "result", summary: "" })] }] } as never))),
  );
  renderAt(`/cases/${CASE_A}/timeline`);
  expect((await screen.findAllByText(/Zz lab result/)).length).toBeGreaterThan(0);
});
