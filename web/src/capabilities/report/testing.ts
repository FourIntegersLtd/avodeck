// Test stand-ins for slice F's API: typed bodies built from the generated schema, with section keys, checklist
// items, declarations and step names taken from the pack fixtures (never written here).
import { HttpResponse } from "msw";
import type { Schemas } from "../../foundation/platform-client";
import { http, json, server } from "../../test/api";
import { casePack, inCase, type PackRun } from "../setup/testing";

export { casePack, inCase };
export type { PackRun };

export const V1 = "b1111111-1111-4111-8111-111111111111";
export const V2 = "b2222222-2222-4222-8222-222222222222";
export const INV = "c1111111-1111-4111-8111-111111111111";
export const SPOT = "d1111111-1111-4111-8111-111111111111";
export const PARA = "e1111111-1111-4111-8111-111111111111";

export const sectionKeys = (run: PackRun) => casePack(run).report_sections.map((s) => String((s as { key: string }).key));
/** The heading the pack gives a report section (screens 16 and 17 show it, never the key). */
export const sectionLabel = (run: PackRun, key: string) => String((casePack(run).report_sections as Array<{ key: string; label?: string }>).find((s) => s.key === key)?.label);
export const stepKeys = (run: PackRun) => casePack(run).workflow_steps.map((s) => String((s as { key: string }).key));
export const declarations = (run: PackRun) => (casePack(run) as unknown as { declarations: Array<{ key: string; text: string }> }).declarations;
export const checklistItems = (run: PackRun) => (casePack(run) as unknown as { compliance_checklist: { items: Array<{ key: string; text?: string; text_key?: string }> } }).compliance_checklist.items;

export function versionOut(over: Partial<Schemas["VersionOut"]> = {}): Schemas["VersionOut"] {
  return {
    id: V1, version_no: 1, type: "report", display_name: "Draft 1", status: "draft", created_at: "2026-10-18T09:00:00Z",
    page_count: 31, citation_style: "page", template_fallback: false, ...over,
  };
}

export function reportOut(run: PackRun, over: Partial<Schemas["ReportOut"]> = {}): Schemas["ReportOut"] {
  const version = versionOut();
  const items = checklistItems(run);
  return {
    checklist: { rule_ref: "ref", items: items.map((item, i) => ({ key: item.key, text: item.text ?? null, text_key: item.text_key ?? null, done: i === 0 })) },
    sections: sectionKeys(run).map((key, i) => ({ key, written: i === 0, generated: i !== 0 })),
    version,
    versions: [version],
    ...over,
  };
}

export const settingsOut = (over: Partial<Schemas["SettingsOut"]> = {}): Schemas["SettingsOut"] => ({ addressed: [], citation_style: "page", job_id: null, version: 3, ...over });

export function checkResult(over: Partial<Schemas["CheckResult"]> = {}): Schemas["CheckResult"] {
  return { key: "all_pages_read", source: "core", outcome: "passed", count: 0, fix: null, fixes: [], text: "Every page read by you", ...over };
}

export function finalCheck(over: Partial<Schemas["FinalCheckOut"]> = {}): Schemas["FinalCheckOut"] {
  return {
    id: "f1111111-1111-4111-8111-111111111111", report_version_id: V1, at: "2026-10-18T10:00:00Z", passed: true, current: true,
    declaration_changed: false, pack_versions: {}, warnings: [], warnings_status: "done",
    results: [checkResult(), checkResult({ key: "citations_resolve", text: "Every fact traced to a page you checked", count: 214 })], ...over,
  };
}

export function failedCheck(): Schemas["FinalCheckOut"] {
  const fix = { screen: "opinion", item: "q4" };
  return finalCheck({
    passed: false,
    results: [
      checkResult(),
      checkResult({ key: "questions_answered", outcome: "failed", count: 1, text: "Question 4 hasn't been answered yet.", fix, fixes: [fix] }),
      checkResult({ key: "age_agrees", outcome: "not_run", code: "no_date_of_birth", text: "The age agrees with the date of birth" }),
    ],
  });
}

export const spotOut = (over: Partial<Schemas["SpotCheckOut"]> = {}): Schemas["SpotCheckOut"] => ({
  id: SPOT, created_at: "2026-10-18T10:00:00Z", expires_at: "2026-10-25T10:00:00Z", fact_count: 10, locked: false, opened_at: null,
  report_version_id: V1, responded_at: null, responses: [], reviewer_email: "colleague@example.test", ...over,
});

export const invoiceOut = (over: Partial<Schemas["InvoiceOut"]> = {}): Schemas["InvoiceOut"] => ({
  id: INV, number_text: null, status: "draft", currency: "GBP", created_at: "2026-10-18T10:00:00Z", sent_at: null, sent_to: null,
  paid_amount: "0.00", tax: "0.00", total: "3645.00", credit_notes: [], statements: [], estimate_amount: null,
  lines: [{ kind: "time", seconds: 72900, hours: "20.25", rate: "180.00", amount: "3645.00" }, { kind: "expenses", amount: "0.00" }], ...over,
});

export const profile = (over: Partial<Schemas["BillingProfile"]> = {}): Schemas["BillingProfile"] => ({
  id: "a9999999-9999-4999-8999-999999999999", legal_name: "Dr Jane Morgan", address: "1 High Street", tax_number: null, tax_rate: "0.00",
  currency: "GBP", hourly_rate: "180.00", invoice_prefix: "JM-", ...over,
});

export type Sent = { method: string; path: string; body: unknown; ifMatch: string | null };

export function etagged(body: unknown, version: number, status = 200) {
  return HttpResponse.json(body as never, { status, headers: { etag: `"${version}"` } });
}

/** Signed in under a pack with a stub for every slice F endpoint a screen reads; `record` notes what was sent. */
export function serve(run: PackRun, opts: {
  report?: Schemas["ReportOut"] | null; settings?: Schemas["SettingsOut"]; check?: Schemas["FinalCheckOut"] | null;
  spots?: Schemas["SpotCheckOut"][]; time?: Schemas["TimeOut"]; profile?: Schemas["BillingProfile"] | null; invoices?: Schemas["InvoiceOut"][];
  servings?: Schemas["ServingOut"][];
} = {}) {
  inCase(run);
  const sent: Sent[] = [];
  const record = async (request: Request) => {
    const text = await request.text();
    sent.push({ method: request.method, path: new URL(request.url).pathname, body: text ? JSON.parse(text) : null, ifMatch: request.headers.get("if-match") });
  };
  const steps = stepKeys(run);
  server.use(
    http.get("/v1/cases/:caseId/report", () => json((opts.report === undefined ? reportOut(run) : opts.report) as never)),
    http.get("/v1/cases/:caseId/report/settings", () => etagged(opts.settings ?? settingsOut(), (opts.settings ?? settingsOut()).version)),
    http.get("/v1/cases/:caseId/report/methodology", () => json({ pages_read: 1231, pages_total: 1231, tasks: [] })),
    http.get("/v1/cases/:caseId/report/final-check/latest", () => (opts.check ? json(opts.check) : HttpResponse.json({ error: { code: "not_found", message: "x" } }, { status: 404 }))),
    http.get("/v1/cases/:caseId/spot-checks", () => json({ spot_checks: opts.spots ?? [] })),
    http.get("/v1/cases/:caseId/report/versions/:v/servings", () => json({ servings: opts.servings ?? [] })),
    http.get("/v1/cases/:caseId/time", () => json(opts.time ?? { adjusted: false, total_seconds: 72900, stages: steps.map((stage, i) => ({ stage, seconds: (i + 1) * 600 })) })),
    http.get("/v1/me/billing-profile", () => etagged({ profile: opts.profile === undefined ? profile() : opts.profile }, opts.profile === null ? 0 : 2)),
    http.get("/v1/cases/:caseId/invoices", () => json({ invoices: opts.invoices ?? [] })),
  );
  return { sent, record };
}
