// Rule 11: screen copy runs under the launch packs and the fixture packs (packs/_fixtures). Party
// words, step names and date order change with the pack; no launch word leaks into fixture screens.
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { signedIn } from "../../test/api";
import { CASE_A, caseCard, fixturePack, launchPack, me, today, type Schemas } from "../../test/fixtures";
import { renderAt, renderUi } from "../../test/render";
import { CaseHeader } from "../ui";

const LAUNCH_WORDS = /defendant|claimant|solicitor|\bGPs?\b|trial bundle|statement of truth/i;

const runs: Array<{ name: string; pack: Schemas["CasePack"]; party: string; step: string; due: string; steps: string[] }> = [
  { name: "launch packs", pack: launchPack, party: "For the defendant", step: "Read", due: "30 Nov", steps: ["Prepare", "Read", "Understand", "Write", "Court"] },
  { name: "fixture packs", pack: fixturePack, party: "For the respondent", step: "Review", due: "Nov 30", steps: ["Gather", "Review", "Conclude"] },
];

describe.each(runs)("under the $name", ({ pack, party, step, due, steps }) => {
  test("The Cases row uses the pack's party word, step name and date order", async () => {
    const stage = pack.workflow_steps[1]!.key as string;
    signedIn({
      me: me({ locale: null, timezone: null }),
      today: today({ cases: [caseCard({ case_id: CASE_A, stage, party_role: "other_side", due_date: "2026-11-30" })] }),
      pack,
    });
    renderAt("/cases");
    const card = await screen.findByRole("link", { name: /Mrs A/ });
    expect(await within(card).findByText(party)).toBeInTheDocument();
    expect(within(card).getByText(step)).toBeInTheDocument();
    expect(within(card).getByText(due)).toBeInTheDocument();
    if (pack === fixturePack) expect(document.body.textContent).not.toMatch(LAUNCH_WORDS);
  });

  test("the case header shows the pack's workflow steps", () => {
    renderUi(<CaseHeader steps={pack.workflow_steps as Array<{ key: string }>} current={pack.workflow_steps[1]!.key as string} />);
    const pills = screen.getAllByRole("listitem").map((li) => li.textContent);
    // Each pill is a marker (a number, or a tick once done) and the step's name.
    expect(pills.map((p) => p?.replace(/^[\d✓]+/, "").trim())).toEqual(steps);
  });
});
