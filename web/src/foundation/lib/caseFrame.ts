// What the case frame (app/CaseLayout) gives the screens inside it: the case and its merged pack,
// loaded once, so a slice's case screen does not fetch them again.
import { useOutletContext } from "react-router-dom";
import { formatDue, humanise, t } from "../i18n";
import type { Schemas } from "../platform-client";

export interface CaseFrame {
  item: Schemas["Case"];
  pack: Schemas["CasePack"];
  reload: () => void;
}

/** A case route's handle names its screen key, so the frame can mark the pack step that holds it. */
export interface CaseScreenHandle {
  screen: string;
}

export function useCaseFrame(): CaseFrame {
  return useOutletContext<CaseFrame>();
}

/** The key of the pack step whose screens include `screen`, if any. */
export function stepFor(pack: Schemas["CasePack"], screen: string | undefined): string | null {
  if (!screen) return null;
  const steps = pack.workflow_steps as Array<{ key: string; screens?: string[] }>;
  return steps.find((s) => s.screens?.includes(screen))?.key ?? null;
}

/** Under the case name: "case type · due 30 Nov" (just the type when there is no due date). */
export function caseSubtitle(item: Schemas["Case"], pack: Schemas["CasePack"]): string {
  const type = item.case_type ? humanise(item.case_type) : "";
  return item.due_date && type ? t("ui.case_bar.sub", { type, date: formatDue(item.due_date, pack.locale) }) : type;
}
