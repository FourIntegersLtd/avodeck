// Rule 11: a fact kind and its fields are named in the pack's words; the humanised key is only a fallback.
import { expect, test } from "vitest";
import { fixturePack, launchPack, type Schemas } from "../../test/fixtures";
import { fieldWord, kindWord } from "./packWords";

const pack = (fact_kinds: Array<Record<string, unknown>>) => ({ ...launchPack, fact_kinds }) as Schemas["CasePack"];

test("the pack's label wins for a kind and a field", () => {
  const p = pack([{ key: "phone_consultation", label: "Call", field_labels: { clinician_name: "Spoken to by" } }]);
  expect(kindWord(p, "phone_consultation")).toBe("Call");
  expect(fieldWord(p, "phone_consultation", "clinician_name")).toBe("Spoken to by");
});

test("without a label, the key is humanised", () => {
  const p = pack([{ key: "phone_consultation", fields: { safety_netting: "string" } }]);
  expect(kindWord(p, "phone_consultation")).toBe("Phone consultation");
  expect(fieldWord(p, "phone_consultation", "safety_netting")).toBe("Safety netting");
  expect(kindWord(p, "unknown_kind")).toBe("Unknown kind");
  expect(fieldWord(p, "unknown_kind", "done_on")).toBe("Done on");
  expect(kindWord(undefined, "home_visit")).toBe("Home visit");
  expect(fieldWord(null, "home_visit", "plan")).toBe("Plan");
});

test.each([
  ["launch", launchPack],
  ["fixture", fixturePack],
])("every kind and field in the %s packs has its own label", (_, p) => {
  for (const k of p.fact_kinds as Array<{ key: string; label?: string; fields?: Record<string, string>; field_labels?: Record<string, string> }>) {
    expect(k.label, k.key).toBeTruthy();
    for (const f of Object.keys(k.fields ?? {})) expect(k.field_labels?.[f], `${k.key}.${f}`).toBeTruthy();
  }
});
