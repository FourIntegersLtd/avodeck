// Pieces the Today and Cases screens share: packs for the cases shown, the step ring, the balance footer.
import { useMemo } from "react";
import { humanise, t } from "../../foundation/i18n";
import { GLYPHS, type Glyph } from "../../foundation/lib/needsYouIcons";
import { firstScreenInCase } from "../../foundation/lib/screens";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { ProgressRing } from "../../foundation/ui";

export type CasePack = Schemas["CasePack"];
export type CaseCard = Schemas["CaseCard"];

export const combination = (c: CaseCard) => `${c.jurisdiction}/${c.case_type}/${c.specialty}`;

/** Where a case opens: its home when a slice registers one, else the bare case address (which finds it). */
export const caseHref = (id: string) => firstScreenInCase(["case_home"], id) ?? `/cases/${id}`;

/** One pack per combination of jurisdiction, case type and specialty among the cards. */
export function usePacks(cases: CaseCard[] | undefined) {
  const firstOfEach = useMemo(() => {
    const seen = new Map<string, string>();
    for (const c of cases ?? []) if (!seen.has(combination(c))) seen.set(combination(c), c.case_id);
    return [...seen.entries()];
  }, [cases]);
  return useResource(async () => {
    const loaded = await Promise.all(
      firstOfEach.map(async ([combo, caseId]) => [combo, await unwrap(api.GET("/v1/cases/{case_id}/pack", { params: { path: { case_id: caseId } } }))] as const),
    );
    return new Map<string, CasePack>(loaded);
  }, [firstOfEach]);
}

/** "This year: 14 for the …, 9 for the …": how many cases the expert took for each side, in the packs' words. */
export function BalanceFooter({ packs }: { packs: Map<string, CasePack> | undefined }) {
  const found = useResource(() => unwrap(api.GET("/v1/me/stats/balance", { params: { query: { months: 12 } } })), [], { cache: "today/Today#BalanceFooter:found" });
  if (found.error || !found.data) return null;
  const words: Record<string, string> = {};
  for (const pack of packs?.values() ?? []) for (const r of pack.party_roles) words[r.key!] = r.label!;
  const parts = Object.entries(found.data.counts)
    .filter(([, count]) => count > 0)
    .map(([role, count]) => `${count} ${t("setup.balance.side", { party: words[role] ?? humanise(role) })}`);
  if (parts.length === 0) return null;
  return (
    <p className="av-muted av-today__balance" data-testid="today-balance">
      {t("today.balance", { list: parts.join(", ") })}
    </p>
  );
}

/** How far along a case is: the design system's ring filled to the pack step, with the step's name inside. */
export function StepRing({ step, number, total }: { step: string; number: number; total: number }) {
  return (
    <ProgressRing value={number} max={total} label={t("case.step_ring", { step, number, total })}>
      {step}
    </ProgressRing>
  );
}

/** A line icon on a 24px grid. */
export function Icon({ glyph, d, size = 20 }: { glyph?: Glyph; d?: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" data-glyph={glyph}>
      <path d={glyph ? GLYPHS[glyph] : d} />
    </svg>
  );
}
