// Rule 11: fact kinds and their fields in the case's own pack words. A pack names each kind
// (`label`, or an i18n `label_key`) and each field (`field_labels`); the humanised key is only a fallback.
import { hasMessage, humanise, t } from "../i18n";
import type { Schemas } from "../platform-client";

type Kind = { key?: unknown; label?: unknown; label_key?: unknown; field_labels?: unknown };

function kindOf(pack: Schemas["CasePack"] | null | undefined, kind: string): Kind | undefined {
  return (pack?.fact_kinds as Kind[] | undefined)?.find((k) => k.key === kind);
}

const word = (value: unknown) => (typeof value === "string" && value.trim() ? value : null);

/** The pack's word for a fact kind ("phone_consultation" -> the pack's label, else "Phone consultation"). */
export function kindWord(pack: Schemas["CasePack"] | null | undefined, kind: string): string {
  const k = kindOf(pack, kind);
  const label = word(k?.label);
  if (label) return label;
  const key = word(k?.label_key);
  if (key && hasMessage(key)) return t(key);
  return humanise(kind);
}

/** The pack's word for one field of a fact kind, else the field's name in plain form. */
export function fieldWord(pack: Schemas["CasePack"] | null | undefined, kind: string, field: string): string {
  const labels = kindOf(pack, kind)?.field_labels as Record<string, unknown> | undefined;
  return word(labels?.[field]) ?? humanise(field);
}
