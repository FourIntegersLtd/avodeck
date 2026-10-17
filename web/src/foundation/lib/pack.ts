import { humanise, t } from "../i18n";
import type { Schemas } from "../platform-client";

/** The pack's word for a party role key ("other_side" -> the pack's label), for "For the …". */
export function partyLine(pack: Schemas["CasePack"] | undefined, role: string | null | undefined): string | null {
  if (!pack || !role) return null;
  const label = pack.party_roles.find((r) => r.key === role)?.label;
  if (!label) return null;
  // A joint instruction has no "side": show the pack's words on their own.
  return role === "joint" ? humanise(label) : t("case.party", { party: label });
}
