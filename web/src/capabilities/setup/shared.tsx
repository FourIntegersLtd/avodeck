// Parts shared by slice C's screens: choices, a labelled box, the balance of sides, a drafted note and
// file uploads. Words come from keys or from the pack (rule 11); files go only to FastAPI (rule 4).
import { useId, type ButtonHTMLAttributes, type ChangeEvent, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { hasMessage, humanise, t } from "../../foundation/i18n";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { ChoicePills, Segmented, StackedBar } from "../../foundation/ui";

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
      <Group label={legend} name={name} options={options} value={value} onChange={onChange} />
      {hint && <p className="av-field__hint">{hint}</p>}
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
export function Section({ id, title, intro, mark, action, children }: { id: string; title: string; intro?: string; mark?: ReactNode; action?: ReactNode; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="av-card av-stack">
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
  const found = useResource(() => unwrap(api.GET("/v1/me/stats/balance", { params: { query: { months: BALANCE_MONTHS } } })), []);
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

const hex = (buffer: ArrayBuffer) => [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");

/** Sends a file to the API in parts and returns the document it became. */
export async function uploadFile(caseId: string, file: File, kind: string): Promise<string> {
  const bytes = await bytesOf(file);
  // A typed array view: a FileReader result can come from another realm, which digest refuses as a bare ArrayBuffer.
  const sha256 = hex(await crypto.subtle.digest("SHA-256", new Uint8Array(bytes)));
  const started = await unwrap(
    api.POST("/v1/cases/{case_id}/uploads", { params: { path: { case_id: caseId } }, body: { filename: file.name, kind, sha256, size: bytes.byteLength } }),
  );
  for (let n = 1; n <= started.chunk_count; n += 1) {
    const part = bytes.slice((n - 1) * started.chunk_size, n * started.chunk_size);
    await unwrap(
      api.PUT("/v1/uploads/{upload_id}/chunks/{n}", {
        params: { path: { upload_id: started.upload_id, n } },
        body: part as never,
        bodySerializer: (b: unknown) => b as BodyInit,
        headers: { "Content-Type": "application/octet-stream" },
      }),
    );
  }
  const done = await unwrap(api.POST("/v1/uploads/{upload_id}/complete", { params: { path: { upload_id: started.upload_id } } }));
  return String(done.document_id);
}

/** A file's bytes, for the raw-body uploads (template, signature). */
export const fileBytes = bytesOf;

/** A file input that looks like a button; the input itself stays the labelled control. */
export function FileButton({ label, accept, onChange, link }: { label: string; accept: string; onChange: (e: ChangeEvent<HTMLInputElement>) => void; link?: boolean }) {
  const id = useId();
  return (
    <div className="av-setup-file">
      <label htmlFor={id} className={link ? "av-text-link av-setup-file__label" : "av-button av-button--secondary av-setup-file__label"}>
        {label}
      </label>
      <input id={id} type="file" accept={accept} className="av-visually-hidden" onChange={onChange} />
    </div>
  );
}

/** The arrow of a call to action that moves on, for a button that does something first. */
export function ArrowIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}
