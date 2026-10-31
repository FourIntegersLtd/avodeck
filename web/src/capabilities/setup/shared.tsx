// Parts shared by slice C's screens: choices, a labelled box, the balance of sides, a drafted note and
// file uploads. Words come from keys or from the pack (rule 11); files go only to FastAPI (rule 4).
import { useId, type ButtonHTMLAttributes, type ChangeEvent, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { hasMessage, humanise, t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { uploadInParts } from "../../foundation/lib/chunkedUpload";
import { sw } from "./words";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { ChoicePills, Segmented, Spinner, StackedBar } from "../../foundation/ui";

/** Waits for a client call and returns its body with the ETag it carried (for If-Match later). */
export async function withEtag<D>(call: Promise<{ data?: D; error?: unknown; response: Response }>): Promise<{ data: D; etag: string }> {
  const data = await unwrap(call);
  const { response } = await call;
  return { data, etag: response.headers.get("etag") ?? "" };
}

/** A version number as the ETag the API sends for it. */
export const versionTag = (version: number) => `"${version}"`;

/** The pack's word for a key, with a capital first letter ("other_side" -> "Respondent"). */
export function packWord(words: unknown, key: string): string {
  const found = words && typeof words === "object" ? (words as Record<string, unknown>)[key] : undefined;
  return humanise(typeof found === "string" ? found : key);
}

/** Party role words from a case pack, as a key -> word map. */
export function partyWords(pack: Schemas["CasePack"]): Record<string, string> {
  return Object.fromEntries(pack.party_roles.map((r) => [r.key!, r.label!]));
}

export interface Option {
  value: string;
  label: string;
}

/** A question with pill answers (one choice). `segmented` gives the small switch for a pair such as the side. */
export function Choice({
  legend,
  hint,
  name,
  options,
  value,
  onChange,
  segmented,
  children,
}: {
  legend: string;
  hint?: string;
  name: string;
  options: Option[];
  value: string | null;
  onChange: (value: string) => void;
  segmented?: boolean;
  children?: ReactNode;
}) {
  const Group = segmented ? Segmented : ChoicePills;
  return (
    <div className="av-setup-choice av-stack">
      <p className="av-setup-q-label" aria-hidden="true">
        {legend}
      </p>
      {hint && <p className="av-setup-q-hint">{hint}</p>}
      <Group label={legend} hideLabel name={name} options={options} value={value} onChange={onChange} />
      {children}
    </div>
  );
}

export function TextArea({ label, hint, error, ...box }: { label: string; hint?: string; error?: string } & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id">) {
  const id = useId();
  const described = [hint && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(" ") || undefined;
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
      {error && (
        <p className="av-field__error" id={`${id}-error`}>
          {error}
        </p>
      )}
      <textarea id={id} className="av-textarea av-setup-textarea" aria-invalid={error ? true : undefined} aria-describedby={described} {...box} />
    </div>
  );
}

export function Select({ label, options, ...select }: { label: string; options: Option[] } & Omit<SelectHTMLAttributes<HTMLSelectElement>, "id">) {
  const id = useId();
  return (
    <div className="av-field">
      <label className="av-field__label" htmlFor={id}>
        {label}
      </label>
      <select id={id} className="av-input" {...select}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

/**
 * A section with a level-2 heading that names it (a landmark the tests and screen readers can find).
 * `mark` sits before the heading (a tick when the part is done) and `action` at the right of it.
 */
export function Section({ id, title, intro, mark, action, current, children }: { id: string; title: string; intro?: string; mark?: ReactNode; action?: ReactNode; current?: boolean; children: ReactNode }) {
  return (
    <section aria-labelledby={id} aria-current={current ? "step" : undefined} className={`av-card av-stack${current ? " av-setup-current" : ""}`}>
      <div className="av-stack av-setup-sechead">
        <div className="av-row av-row--spread">
          <div className="av-row">
            {mark}
            <h2 id={id}>{title}</h2>
          </div>
          {action}
        </div>
        {intro && <p className="av-muted">{intro}</p>}
      </div>
      {children}
    </section>
  );
}

/** A dashed box that explains an empty part in plain words, with one secondary button to start it. */
export function EmptyBox({ title, text, children }: { title: string; text: string; children: ReactNode }) {
  return (
    <div className="av-setup-empty">
      <div className="av-setup-empty__text">
        <strong>{title}</strong>
        <p className="av-muted">{text}</p>
      </div>
      {children}
    </div>
  );
}

/** The round mark before a section's title: a tick once the part is done, else its number. */
export function PartMark({ done, number }: { done: boolean; number: number }) {
  return (
    <span className={`av-setup-mark${done ? " av-setup-mark--done" : ""}`} aria-hidden="true">
      {done ? (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" focusable="false">
          <path d="M5 12l5 5 9-10" />
        </svg>
      ) : (
        number
      )}
    </span>
  );
}

/** A button that reads as a link ("Edit", "Replace"): it does something here instead of opening a page. */
export function LinkAction({ className, ...rest }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" className={["av-text-link", "av-setup-linkbtn", className].filter(Boolean).join(" ")} {...rest} />;
}

/** How often the expert has acted for each side lately (judges notice one-sided experts). */
const BALANCE_MONTHS = 12;
const BALANCE_COLOURS = ["var(--green)", "var(--green-bright)", "var(--line-strong)"];

/** The heading shows at once; the counts fill in when they arrive. */
export function Balance({ words }: { words: Record<string, string> }) {
  const found = useResource(() => unwrap(api.GET("/v1/me/stats/balance", { params: { query: { months: BALANCE_MONTHS } } })), [], { cache: "setup/shared#Balance:found" });
  if (found.error) return null;
  const counts = Object.entries(found.data?.counts ?? {});
  const title = t("setup.balance.title", { months: found.data?.months ?? BALANCE_MONTHS });
  return (
    <section aria-labelledby="setup-balance" className="av-card av-stack av-setup-balance">
      <div className="av-row av-row--spread">
        <h2 id="setup-balance">{title}</h2>
        <p className="av-muted">{t("setup.balance.intro")}</p>
      </div>
      <StackedBar
        label={title}
        segments={counts.map(([role, count], i) => ({
          key: role,
          value: count,
          color: BALANCE_COLOURS[i % BALANCE_COLOURS.length]!,
          label: t("setup.balance.side", { party: words[role] ?? humanise(role) }),
        }))}
      />
    </section>
  );
}

/** A note drafted for whoever instructed the expert: the words to send, for the expert to copy. */
export function NoteView({ note }: { note: Schemas["NoteDraft"] }) {
  return (
    <section aria-labelledby="setup-note" className="av-card av-stack">
      <h2 id="setup-note">{t("setup.notes.title")}</h2>
      <div className="av-rows">
        <div className="av-row-item">
          <span className="av-muted">{t("setup.notes.to")}</span>
          <ul className="av-setup-plain">
            {note.recipients.map((r) => (
              <li key={r.id}>{t("setup.party_line", { name: r.name, firm: r.firm })}</li>
            ))}
          </ul>
        </div>
        <div className="av-row-item">
          <span className="av-muted">{t("setup.notes.subject")}</span>
          <strong>{t(note.subject_key)}</strong>
        </div>
      </div>
      <p>{t(note.body_key)}</p>
      {note.items.length > 0 && (
        <ul className="av-setup-plain">
          {note.items.map((item, i) => (
            <li key={item.question_id ?? i}>
              {item.number != null
                ? t("setup.notes.item", { number: item.number, text: item.text ?? "" })
                : [item.label ?? (item.label_key && hasMessage(item.label_key) ? t(item.label_key) : null), item.text].filter(Boolean).join(": ")}
            </li>
          ))}
        </ul>
      )}
      {note.joint && <p className="av-muted">{t("setup.notes.joint")}</p>}
    </section>
  );
}

async function bytesOf(file: Blob): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === "function") return file.arrayBuffer();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(file);
  });
}

/** Sends a file to the API in parts and returns the document it became (checks and retries: foundation/lib/chunkedUpload). */
export async function uploadFile(caseId: string, file: File, kind: string): Promise<string> {
  return uploadInParts(caseId, file, kind);
}

export interface UploadOutcome {
  saved: Array<{ file: File; documentId: string }>;
  failed: Array<{ name: string; reason: string }>;
}

/** Uploads each file (then runs `after` on it) and carries on past one that fails; says which were saved and which not. */
export async function uploadFiles(
  caseId: string,
  files: File[],
  kind: string,
  after?: (file: File, documentId: string) => Promise<void>,
): Promise<UploadOutcome> {
  const out: UploadOutcome = { saved: [], failed: [] };
  for (const file of files) {
    try {
      const documentId = await uploadFile(caseId, file, kind);
      await after?.(file, documentId);
      out.saved.push({ file, documentId });
    } catch (err) {
      out.failed.push({ name: file.name, reason: errorMessage(err) });
    }
  }
  return out;
}

/** Plain words for an upload outcome that had failures (null when every file was saved). */
export function uploadSummary(out: UploadOutcome): string | null {
  if (out.failed.length === 0) return null;
  const parts = [sw("upload.not_saved", { files: out.failed.map((f) => `${f.name} (${f.reason})`).join("; ") })];
  if (out.saved.length > 0) parts.unshift(sw("upload.saved", { files: out.saved.map((s) => s.file.name).join(", ") }));
  return parts.join(" ");
}

/** A file's bytes, for the raw-body uploads (template, signature). */
export const fileBytes = bytesOf;

/** A file input that looks like a button; the input itself stays the labelled control. */
export function FileButton({ label, accept, onChange, link, busy }: { label: string; accept: string; onChange: (e: ChangeEvent<HTMLInputElement>) => void; link?: boolean; busy?: boolean }) {
  const id = useId();
  return (
    <div className="av-setup-file">
      <label htmlFor={id} aria-busy={busy || undefined} className={link ? "av-text-link av-setup-file__label" : "av-button av-button--secondary av-setup-file__label"}>
        {busy && <Spinner />}
        {label}
      </label>
      <input id={id} type="file" accept={accept} className="av-visually-hidden" disabled={busy} onChange={onChange} />
    </div>
  );
}
