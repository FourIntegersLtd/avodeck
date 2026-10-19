// Every step pill in the case frame shows its own progress: "N%" for big counts, "x of y" for small ones.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, json, server, signedIn } from "../test/api";
import { CASE_A, launchPack, PERSONAL_ORG, PERSON_ID, type Schemas } from "../test/fixtures";
import { renderAt } from "../test/render";

const theCase = {
  id: CASE_A, organisation_id: PERSONAL_ORG, named_expert_id: PERSON_ID, jurisdiction: "gb-eaw", case_type: "clin-neg-breach",
  specialty: "general-practice", expert_role: "single", label: "Mrs A", party_role: "other_side", instructing_party_type: null,
  court_track: null, due_date: "2026-11-30", status: "open", on_hold: false, created_at: "2026-10-01T09:00:00Z",
  updated_at: "2026-10-01T09:00:00Z", allowed_actions: ["case.view_content"],
} as Schemas["Case"];
const keys = (launchPack.workflow_steps as Array<{ key: string }>).map((s) => s.key);

test("each pill with something to count shows it, and the rest show nothing", async () => {
  signedIn();
  const entries = keys.map((key, i) =>
    i === 0 ? { key, done: 3, total: 5, percent: 60 }
    : i === 1 ? { key, done: 1104, total: 1231, percent: 90 }
    : i === 2 ? { key, done: 0, total: 0, percent: null }
    : { key, done: null, total: null, percent: null });
  server.use(
    http.get(`/v1/cases/${CASE_A}`, () => json(theCase)),
    http.get(`/v1/cases/${CASE_A}/steps/progress`, () => json({ steps: entries })),
  );
  renderAt(`/cases/${CASE_A}`);
  const bar = await screen.findByRole("banner");
  expect(await within(bar).findByText("3 of 5")).toBeInTheDocument();
  expect(within(bar).getByText("90%")).toBeInTheDocument();
  expect(within(bar).queryAllByText(/%|of/)).toHaveLength(2);
});
