// S11 gaps on a narrow board: where the sentence does not fit along the line, the line shows the short count and
// the full sentence stays for screen readers; where it fits, the sentence shows.
import { render } from "@testing-library/react";
import { expect, test } from "vitest";
import { GapConnectors } from "./GapConnectors";
import { dayMs, groupByDay } from "./storyLayout";
import { event, FACT_1, FACT_2 } from "./testing";

const from = groupByDay("a", [event({ fact_id: FACT_2, event_date: "2024-01-10", has_finding: true })])[0]!;
const to = groupByDay("b", [event({ fact_id: FACT_1, event_date: "2024-03-10" })])[0]!;
const gap = { finding_id: "c1111111-1111-4111-8111-111111111111", from_fact_id: FACT_2, to_fact_id: FACT_1, days: 60, kind: "no_action_within", label_key: "timeline.gap.no_action_within" };
const scale = (pxPerDay: number) => (iso: string) => ((dayMs(iso) - dayMs("2024-01-01")) / 86_400_000) * pxPerDay;

function visibleText(pxPerDay: number) {
  const toX = scale(pxPerDay);
  const { container } = render(<GapConnectors cards={[{ group: from, x: toX(from.date), row: 0 }]} all={[from, to]} gaps={[gap]} toX={toX} rowOffset={0} />);
  const shown = [...container.querySelectorAll<HTMLElement>(".av-story-gap span")].filter((s) => !s.classList.contains("av-visually-hidden") && s.getAttribute("aria-hidden") !== "false");
  return { shown: shown.map((s) => s.textContent), all: container.textContent };
}

test("a short line shows the count, and the sentence stays for screen readers", () => {
  const { shown, all } = visibleText(4);
  expect(shown).toEqual(["60 days"]);
  expect(all).toContain("60 days. Nothing in the record shows what followed.");
});

test("a long line shows the sentence", () => {
  expect(visibleText(12).shown).toEqual(["60 days. Nothing in the record shows what followed."]);
});
