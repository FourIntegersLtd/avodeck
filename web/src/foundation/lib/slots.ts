// Slots: places in foundation screens where capabilities register their own parts, so the foundation
// never names a slice. Account sections (S28) and case header actions.
import type { ComponentType } from "react";

export interface AccountSection {
  key: string;
  /** Lower comes first; sections follow the foundation's own. */
  order: number;
  /** i18n key of the section's heading. */
  titleKey: string;
  component: ComponentType;
}

/** What the case header already knows about the case and the viewer. */
export interface CaseActionContext {
  caseId: string;
  status: string;
  served: boolean;
  onHold: boolean;
  /** `allowed_actions` from `GET /cases/{id}` (the viewer's case role is reflected here). */
  allowedActions: string[];
  isNamedExpert: boolean;
  /** The viewer's role in the case's organisation, or null. */
  orgRole: string | null;
}

export interface CaseAction {
  key: string;
  order: number;
  /** i18n key of the link's words. */
  labelKey: string;
  to: (ctx: CaseActionContext) => string;
  /** Shown when true; omitted means always. The server still decides what the action may do. */
  visible?: (ctx: CaseActionContext) => boolean;
}

const sections = new Map<string, AccountSection>();
const actions = new Map<string, CaseAction>();

export function registerAccountSection(section: AccountSection): void {
  sections.set(section.key, section);
}

export function accountSections(): AccountSection[] {
  return [...sections.values()].sort((a, b) => a.order - b.order);
}

export function registerCaseAction(action: CaseAction): void {
  actions.set(action.key, action);
}

export function caseActions(ctx: CaseActionContext): Array<{ key: string; labelKey: string; to: string }> {
  return [...actions.values()]
    .sort((a, b) => a.order - b.order)
    .filter((a) => !a.visible || a.visible(ctx))
    .map((a) => ({ key: a.key, labelKey: a.labelKey, to: a.to(ctx) }));
}

/** Forget every registered slot. Tests use it to prove the screens with nothing registered. */
export function resetSlots(): void {
  sections.clear();
  actions.clear();
}
