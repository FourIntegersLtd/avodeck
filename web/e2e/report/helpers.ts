// Slice F's e2e stub: the built app against a stubbed API (page.route), under either pack set.
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, type Route } from "@playwright/test";
import packFixtures from "../../src/foundation/platform-client/pack-fixtures.json" with { type: "json" };
import type { Schemas } from "../../src/foundation/platform-client";

export const CASE_A = "44444444-4444-4444-8444-444444444444";
const ORG = "22222222-2222-4222-8222-222222222222";
const PERSON = "11111111-1111-4111-8111-111111111111";
const VERSION = "b1111111-1111-4111-8111-111111111111";
const INVOICE = "c1111111-1111-4111-8111-111111111111";
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

type Pack = Schemas["CasePack"];
export const PACKS = [
  { name: "launch packs", pack: packFixtures.launch.case_pack as unknown as Pack },
  { name: "fixture packs", pack: packFixtures.fixture.case_pack as unknown as Pack },
];

export type Stub = { status?: "draft" | "locked" | "served"; check?: "failed" | "passed" | "none"; invoice?: "none" | "draft" | "sent" };

const json = (route: Route, body: unknown, status = 200, headers: Record<string, string> = {}) =>
  route.fulfill({ status, contentType: "application/json", headers, body: JSON.stringify(body) });
const JPEG = Buffer.from(
  "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=",
  "base64",
);
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");

export async function stubReport(page: Page, pack: Pack, stub: Stub = {}) {
  const status = stub.status ?? "draft";
  const keys = (pack.report_sections as Array<{ key: string }>).map((s) => s.key);
  const checklist = (pack.compliance_checklist as unknown as { items: Array<{ key: string; text?: string; text_key?: string }> }).items;
  const steps = (pack.workflow_steps as Array<{ key: string }>).map((s) => s.key);
  const party = (pack.instructing_party_types as Array<{ key: string }>)[0]!.key;
  const version = { id: VERSION, version_no: 1, type: "report", display_name: "Draft 1", status, created_at: "2026-10-18T09:00:00Z", page_count: 31, citation_style: "page", template_fallback: false };
  const fix = { screen: "opinion", item: "q4" };
  const check = (): Schemas["FinalCheckOut"] => {
    const results: Schemas["CheckResult"][] = Array.from({ length: 8 }, (_, i) => ({
      key: `check_${i}`, source: "core", outcome: "passed", count: i < 3 ? 214 - i * 40 : 0, fixes: [], text: `Passed check number ${i + 1}`,
    }));
    if (stub.check === "failed") results.push({ key: "questions_answered", source: "core", outcome: "failed", count: 1, fix, fixes: [fix], text: "Question 4 hasn't been answered yet." });
    return {
      id: "f1111111-1111-4111-8111-111111111111", report_version_id: VERSION, at: "2026-10-18T10:00:00Z", passed: stub.check !== "failed", current: true,
      declaration_changed: false, pack_versions: {}, warnings: [], warnings_status: "done", results,
    };
  };
  const invoice = {
    id: INVOICE, number_text: stub.invoice === "sent" ? "JM-2026-031" : null, status: stub.invoice === "sent" ? "sent" : "draft", currency: "GBP",
    created_at: "2026-11-28T10:00:00Z", sent_at: stub.invoice === "sent" ? "2026-11-28T10:00:00Z" : null, sent_to: null, paid_amount: "0.00", tax: "0.00",
    total: "3645.00", credit_notes: [], statements: [], estimate_amount: null,
    lines: [{ kind: "time", seconds: 72900, hours: "20.25", rate: "180.00", amount: "3645.00" }, { kind: "expenses", amount: "0.00" }],
  };
  const theCase = {
    id: CASE_A, organisation_id: ORG, named_expert_id: PERSON, jurisdiction: "gb-eaw", case_type: "clin-neg-breach", specialty: "general-practice", expert_role: "single",
    label: "Mrs A", party_role: "other_side", instructing_party_type: party, court_track: null, due_date: "2026-11-30", status: "open", on_hold: false,
    created_at: "2026-10-01T09:00:00Z", updated_at: "2026-10-01T09:00:00Z",
    allowed_actions: ["case.view_metadata", "case.view_content", "report.write", "report.sign", "report.serve"],
  };
  await page.route("**/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const c = `/v1/cases/${CASE_A}`;
    if (path === "/v1/auth/session") return json(route, { aal: "aal2", mfa_enrolled: true, mfa_complete: true, step_up_until: null, minutes_until_timeout: 30 });
    if (path === "/v1/me") {
      return json(route, {
        person: { id: PERSON, email: "j.morgan@example.test", full_name: "Dr Jane Morgan", locale: "en-GB", timezone: "Europe/London", home_region: "uk", created_at: "2026-01-05T09:00:00Z" },
        expert: null, mfa_enrolled: true, memberships: [{ organisation_id: ORG, name: "Jane Morgan", kind: "personal", role: "owner", started_at: "2026-01-05T09:00:00Z" }],
      });
    }
    if (path === "/v1/notifications") return json(route, { items: [], unread_count: 0, email_bounced: false });
    if (path === c) return json(route, theCase);
    if (path === `${c}/pack`) return json(route, pack);
    if (path === `${c}/report`) {
      return json(route, {
        checklist: { rule_ref: "ref", items: checklist.map((item, i) => ({ key: item.key, text: item.text ?? null, text_key: item.text_key ?? null, done: i !== 1 })) },
        sections: keys.map((key, i) => ({ key, written: i < 2, generated: i >= 2 && i < keys.length - 1, ...(i === 1 ? { done: 2, total: 4 } : {}) })),
        version, versions: [version],
      });
    }
    if (path === `${c}/report/settings`) return json(route, { addressed: [], citation_style: "page", job_id: null, version: 3 }, 200, { etag: '"3"' });
    if (path === `${c}/report/methodology`) return json(route, { pages_read: 1231, pages_total: 1231, tasks: [] });
    if (path === `${c}/report/paragraphs`) {
      const section = new URL(route.request().url()).searchParams.get("section") ?? keys[0]!;
      return json(route, {
        version_id: VERSION,
        paragraphs: [
          { para_no: 1, section, text: "On 19 February 2024 a full blood count showed haemoglobin of 98 g/L and ferritin of 9 ug/L. The result was filed. No action was recorded.", question_id: null },
          { para_no: 2, section, text: "Ferrous fumarate was started on 1 March 2024.", question_id: null },
        ],
      });
    }
    if (path.startsWith(`${c}/report/preview/`)) return route.fulfill({ status: 200, contentType: "image/jpeg", body: JPEG });
    if (path === `${c}/report/final-check/latest`) return stub.check === "none" ? json(route, { error: { code: "not_found", message: "" } }, 404) : json(route, check());
    if (path === `${c}/spot-checks`) return json(route, { spot_checks: [] });
    if (path === `${c}/report/versions/${VERSION}/servings`) return json(route, { servings: [{ id: "s1", served_on: "2026-11-28", sent_via: "email", created_at: "2026-11-28T10:00:00Z", recipient_party_id: null, correction_of: null, reason: null }] });
    if (path === `${c}/time`) {
      return json(route, {
        adjusted: false, total_seconds: 72900, estimate_hours: "22.00", over_estimate: null, instructing_party: "the other party",
        stages: steps.map((stage, i) => ({ stage, seconds: [34800, 11400, 22800, 3900][i % 4]!, estimate_hours: null, estimate_fee: null })),
      });
    }
    if (path === `${c}/invoices`) return json(route, { invoices: stub.invoice === "none" ? [] : [invoice] });
    if (path === "/v1/me/profile") {
      return json(route, { full_name: "Dr James Morgan", profession: "Doctor", registration_body: "Body", registration_number: "123", specialties: [], currency: null, default_jurisdiction: null, default_retention: null, hourly_rate: null, version: 1 });
    }
    if (path === "/v1/me/signature/image") return route.fulfill({ status: 200, contentType: "image/png", body: PNG });
    if (path === "/v1/me/billing-profile") {
      return json(route, { profile: { id: "a9999999-9999-4999-8999-999999999999", legal_name: "Dr James Morgan", address: "1 High Street", tax_number: null, tax_rate: "0.00", currency: "GBP", hourly_rate: "180.00", invoice_prefix: "JM-" } }, 200, { etag: '"2"' });
    }
    return json(route, { error: { code: "not_found", message: "" } }, 404);
  });
}

/** axe with colour contrast, buttons at least 52px tall and body text at least 17px. */
export async function checkScreen(page: Page) {
  const axe = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  expect(axe.violations).toEqual([]);
  const short = await page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>("button, a.av-button"))
      .filter((el) => el.offsetParent !== null)
      .filter((el) => el.getBoundingClientRect().height < 52)
      .map((el) => el.textContent?.trim()),
  );
  expect(short).toEqual([]);
  const small = await page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>("body, main p, main li, main td, main th, main label"))
      .filter((el) => parseFloat(getComputedStyle(el).fontSize) < 17)
      .map((el) => el.tagName + ":" + (el.textContent ?? "").trim().slice(0, 30)),
  );
  expect(small).toEqual([]);
}
