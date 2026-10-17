// What slice A's screens read from the API, beyond the generated types.
import type { Schemas } from "../../foundation/platform-client";

/** `BundleSummary.documents` items (the OpenAPI schema leaves them open because the API omits
 * `media_type`/`part_of` until a file has been sniffed). */
export interface BundleDocument {
  id: string;
  kind: string;
  filename: string;
  pages: number | null;
  first_page_no: number | null;
  last_page_no: number | null;
  received_on: string | null;
  obtained_how: string | null;
  bundle_version: number;
  media_type?: string;
  part_of?: string | null;
}

export const documentsOf = (s: Schemas["BundleSummary"]) => s.documents as unknown as BundleDocument[];

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
