// What slice A's screens read from the API, beyond the generated types.
import type { Schemas } from "../../foundation/platform-client";

/** The bundle's files (`media_type` and `part_of` appear once a file has been sniffed). */
export const documentsOf = (s: Schemas["BundleSummary"]): Schemas["Document"][] => s.documents;

export const casePath = (caseId: string) => ({ params: { path: { case_id: caseId } } });

export const can = (item: Schemas["Case"], action: string) => (item.allowed_actions ?? []).includes(action);

/** The pack's request words (label shown on buttons and the screen title). */
export function requestLabel(pack: Schemas["CasePack"]): string | null {
  const request = (pack.vocabulary as Record<string, unknown>).instructing_party_request as { label?: string } | undefined;
  return request?.label ?? null;
}

/** The pack's words for a source type key, if it gives any. */
export function sourceWord(pack: Schemas["CasePack"], key: string): string | undefined {
  const words = (pack.vocabulary as Record<string, unknown>).source_types as Record<string, string> | undefined;
  return words?.[key];
}

/** The pack's word for the case's instructing party ("counsel", ...), if it gives one. */
export function partyWord(pack: Schemas["CasePack"], item: Schemas["Case"]): string | null {
  const types = (pack.instructing_party_types ?? []) as Array<{ key: string; label?: string }>;
  return types.find((p) => p.key === item.instructing_party_type)?.label ?? null;
}
