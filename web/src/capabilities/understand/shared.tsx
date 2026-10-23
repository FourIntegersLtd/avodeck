// Parts shared by slice D's screens: labelled boxes, dates and the status badge for a fact.
import { createContext, useContext, useId, type ReactNode, type TextareaHTMLAttributes } from "react";
import { useParams } from "react-router-dom";
import { humanise } from "../../foundation/i18n";
import { useResource } from "../../foundation/lib/useResource";
import { kindWord as packKindWord } from "../../foundation/lib/packWords";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Notice } from "../../foundation/ui";
import type { Status } from "../../foundation/ui/StatusBadge";
import { w } from "./words";

export const casePath = (caseId: string) => ({ params: { path: { case_id: caseId } } });

/** "2024-05-15" -> "15 May 2024" (a calendar date, never shifted by timezone). */
export function longDate(iso: string | null | undefined): string {
  if (!iso) return w("common.no_date");
  const [y, m, d] = iso.split("-").map(Number);
  return LONG_DATE.format(new Date(Date.UTC(y!, (m ?? 1) - 1, d ?? 1)));
}

/** Built once: a long timeline formats a date per entry, and making a formatter each time is slow. */
const LONG_DATE = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

/** A fact's status in glossary terms: edited counts as checked by the expert. */
export const statusOf = (status: string): Status => (status === "verified" || status === "edited" ? "checked" : "please_check");

/** The pack names fact kinds by key; with no words for one we show the key in plain letters. */
export const kindWord = (kind: string) => humanise(kind);

const PackContext = createContext<Schemas["CasePack"] | null>(null);

/** Gives a screen the case's pack, so fact kinds show in the pack's own words (rule 11). */
export function PackScope({ children }: { children: ReactNode }) {
  const { caseId = "" } = useParams();
  const pack = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/pack", casePath(caseId))), [caseId]);
  return <PackContext.Provider value={pack.data ?? null}>{children}</PackContext.Provider>;
}

/** The pack's word for a fact kind, else the plain-letters fallback while the pack loads. */
export function useKindWord(): (kind: string) => string {
  const pack = useContext(PackContext);
  return (kind) => packKindWord(pack, kind);
}

/** "2024-05-15" -> "15 May" (the year is left out; the screens that use it sit inside one record). */
export const shortDay = (iso: string | null | undefined): string => (iso ? SHORT_DAY.format(new Date(`${iso}T00:00:00Z`)) : w("common.no_date"));
const SHORT_DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

export const pageLabel = (page: number) => w("common.page", { page });

type DatedEvent = { fact_id: string; page_no: number; event_date?: string | null; available_at?: string | null };

/** Date used to place a dot on the timeline (event day, else when the record became available). */
export function axisIso(e: DatedEvent): string | null {
  return e.event_date ?? e.available_at ?? null;
}

/** Consultation needs a visit day; undated entries open Read on the cited page. */
export function entryHref(caseId: string, event: DatedEvent): string {
  if (axisIso(event)) return `/cases/${caseId}/consultation/${event.fact_id}`;
  return `/cases/${caseId}/read/${event.page_no}?fact=${event.fact_id}`;
}

export function TextArea({ label, hint, ...box }: { label: string; hint?: string } & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id">) {
  const id = useId();
  return (
    <div className="av-field">
      <label className="av-field__label" htmlFor={id}>
        {label}
      </label>
      {hint && (
        <p className="av-field__hint" id={`${id}-hint`}>
          {hint}
        </p>
      )}
      <textarea id={id} className="av-textarea av-understand-textarea" aria-describedby={hint ? `${id}-hint` : undefined} {...box} />
    </div>
  );
}

/** What the signed-in person may do on this case (Case.allowed_actions). Buttons follow it; the server still checks. */
export function useCan(caseId: string): (action: string) => boolean {
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}", casePath(caseId))), [caseId]);
  const actions = found.data?.allowed_actions ?? [];
  return (action: string) => actions.includes(action);
}

/** Shown when guidance the case was first shown has changed in the pack since (codes only). */
export function GuidanceChanged({ codes }: { codes: string[] | undefined }) {
  if (!codes || codes.length === 0) return null;
  return (
    <Notice>
      <p>{w("common.guidance_changed", { codes: codes.join(", ") })}</p>
    </Notice>
  );
}
