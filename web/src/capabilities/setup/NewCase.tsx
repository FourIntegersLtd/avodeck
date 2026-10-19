// S04 New case: who the case is for, the kind of case and the side (in the pack's words), then the case
// details: the letter, what was found in it (suggestions the expert accepts one at a time; the case changes
// only when they save), who instructed them, a check for an earlier case about the same person and anything
// they were told by phone. The server decides everything; this screen shows and sends.
import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { humanise, t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useSession } from "../../foundation/lib/session";
import { useResource } from "../../foundation/lib/useResource";
import { api, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, DictationBox, DropZone, LinkButton, LoadError, Loading, Notice, Spinner, TextField, TextLink } from "../../foundation/ui";
import { sw } from "./words";
import { Choice, FileButton, packWord, partyWords, Section, Select, TextArea, uploadFile, withEtag, type Option } from "./shared";

const trackLabel = (key: string) => t(`setup.court_track.${key}`, {}, humanise(key));

/** Party role choices from a key -> word map; the joint answer, if the pack has one, comes last. */
function sideOptions(words: Record<string, string>): Option[] {
  const keys = Object.keys(words).sort((a, b) => Number(a === "joint") - Number(b === "joint"));
  return keys.map((key) => ({ value: key, label: packWord(words, key) }));
}

export function NewCase() {
  const found = useResource(async () => {
    const packs = await unwrap(api.GET("/v1/packs"));
    const codes = [...new Set(packs.map((p) => p.jurisdiction.code))];
    const jurisdictions = await Promise.all(codes.map((code) => unwrap(api.GET("/v1/jurisdictions/{code}", { params: { path: { code } } }))));
    return { packs, jurisdictions };
  }, []);
  if (found.error) return <div className="av-page"><LoadError onRetry={found.reload} /></div>;
  if (!found.data) return <div className="av-page"><Loading /></div>;
  return <NewCaseForm {...found.data} />;
}

const ICONS = {
  letter: (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M3 7l9 6 9-6" />
    </svg>
  ),
  records: (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d="M7 3h8l4 4v13H7z" />
      <path d="M4 6v15h12" />
    </svg>
  ),
  other: (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden="true" focusable="false">
      <path d="M12 5v14M5 12h14" />
    </svg>
  ),
};

/** The words of a vocabulary map (key -> word), in the pack's order. */
const wordsOf = (map: unknown): Record<string, string> => (map && typeof map === "object" ? (map as Record<string, string>) : {});

type Added = { letters: { name: string; id: string }[]; records: string[]; other: string[] };
type Pages = Record<string, number | null>;

const pagesText = (n: number) => t("setup.new_case.pages", { count: n, n: n.toLocaleString() });

function NewCaseForm({ packs, jurisdictions }: { packs: Schemas["PackCombination"][]; jurisdictions: Schemas["JurisdictionSummary"][] }) {
  const { me } = useSession();
  const navigate = useNavigate();
  const memberships = me?.memberships ?? [];
  const personal = memberships.find((m) => m.kind === "personal") ?? memberships[0];
  const [organisation, setOrganisation] = useState(personal?.organisation_id ?? "");
  const [combo, setCombo] = useState(0);
  const [label, setLabel] = useState("");
  const [side, setSide] = useState<string | null>(null);
  const [track, setTrack] = useState<string | null>(null);
  const [due, setDue] = useState("");
  const [subject, setSubject] = useState<string | null>(null);
  const [instructedBy, setInstructedBy] = useState("");
  const [firm, setFirm] = useState("");
  const [against, setAgainst] = useState("");
  const [oral, setOral] = useState("");
  const [added, setAdded] = useState<Added>({ letters: [], records: [], other: [] });
  const [pages, setPages] = useState<Pages>({});
  const [made, setMade] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const etag = useRef("");
  const sent = useRef({ instructing: false, party: false });
  const pack = packs[combo];
  const place = jurisdictions.find((j) => j.code === pack?.jurisdiction.code);
  const vocabulary = place?.vocabulary ?? {};
  const words = wordsOf(vocabulary.party_roles);
  const partyTypes = wordsOf(vocabulary.instructing_party_types);
  const statuses = Object.entries(wordsOf(vocabulary.subject_statuses));
  const sender = Object.values(partyTypes)[0] ?? "";
  const typeKey = Object.keys(partyTypes)[0] ?? "";

  const fields = () => ({
    label: label.trim() || t("setup.new_case.untitled"),
    party_role: side,
    court_track: track,
    due_date: due || null,
    subject_status: subject,
  });
  /** The case, made the first time something needs it and kept up to date after that. */
  const ensureCase = async (): Promise<string> => {
    if (made) return made;
    if (!pack) throw new Error("no pack");
    const created = await withEtag(
      api.POST("/v1/cases", {
        body: { organisation_id: organisation, jurisdiction: pack.jurisdiction.code, case_type: pack.case_type.code, specialty: pack.specialty.code, ...fields() },
      }),
    );
    etag.current = created.etag;
    setMade(created.data.id);
    return created.data.id;
  };
  const guard = async (work: () => Promise<void>) => {
    setError(null);
    setBusy(true);
    try {
      await work();
    } catch (err) {
      setError(errorMessage(err, { invalid: "setup.new_case.invalid", forbidden: "setup.new_case.forbidden" }));
    } finally {
      setBusy(false);
    }
  };
  /** Saves every answer to the case; the case is made now if nothing needed it before. */
  const saveAll = async (): Promise<string> => {
    const existed = made;
    const id = await ensureCase();
    if (existed) {
      const next = await withEtag(api.PATCH("/v1/cases/{case_id}", { params: { path: { case_id: id } }, body: fields(), headers: ifMatch(etag.current) }));
      etag.current = next.etag;
    }
    const path = { params: { path: { case_id: id } } };
    if (instructedBy.trim() && firm.trim() && !sent.current.instructing) {
      await unwrap(api.POST("/v1/cases/{case_id}/instructing-parties", { ...path, body: { type_key: typeKey, name: instructedBy.trim(), firm: firm.trim() } }));
      sent.current.instructing = true;
    }
    if (against.trim() && !sent.current.party) {
      await unwrap(api.POST("/v1/cases/{case_id}/parties", { ...path, body: { role_key: "other_side", name: against.trim() } }));
      sent.current.party = true;
    }
    if (oral.trim()) {
      await unwrap(api.PUT("/v1/cases/{case_id}/oral-instructions", { ...path, body: { received_on: new Date().toISOString().slice(0, 10), text: oral.trim() } }));
    }
    return id;
  };

  const drop = (kind: "letter" | "records" | "other") => (files: File[]) =>
    void guard(async () => {
      const id = await ensureCase();
      for (const file of files) {
        const path = { params: { path: { case_id: id } } };
        if (kind === "letter") {
          const document_id = await uploadFile(id, file, "instructions");
          await unwrap(api.POST("/v1/cases/{case_id}/instructions", { ...path, body: { kind: added.letters.length > 0 ? "supplementary" : "letter", document_id } }));
          await unwrap(api.POST("/v1/cases/{case_id}/prefill", path));
          setAdded((a) => ({ ...a, letters: [...a.letters, { name: file.name, id: document_id }] }));
        } else {
          const document_id = await uploadFile(id, file, kind);
          setAdded((a) => ({ ...a, [kind]: [...a[kind], document_id] }));
        }
      }
      await countPages(id);
    });
  /** The page counts of the files dropped so far (read from each PDF's page tree; a failure just shows no count). */
  const countPages = async (id: string) => {
    try {
      const files = await unwrap(api.GET("/v1/cases/{case_id}/files", { params: { path: { case_id: id } } }));
      setPages(Object.fromEntries(files.map((f) => [f.document_id, f.page_count])));
    } catch {
      /* the counts are a nicety; the files are safe */
    }
  };
  const apply = (s: Schemas["Suggestion"]) => {
    if (s.field === "instructing_party_name") setInstructedBy(s.value);
    else if (s.field === "party_role") setSide(s.value);
    else if (s.field === "court_track") setTrack(s.value);
    else if (s.field === "due_date") setDue(s.value);
  };
  const prepare = () =>
    void guard(async () => {
      const id = await saveAll();
      await unwrap(api.POST("/v1/cases/{case_id}/process", { params: { path: { case_id: id } } }));
      navigate(`/cases/${id}/conflicts`);
    });
  const later = () =>
    void guard(async () => {
      await saveAll();
      navigate("/");
    });

  return (
    <div className="av-page av-stack">
      <div className="av-row av-row--spread">
        <div>
          <h1>{t("setup.new_case.title")}</h1>
          <p className="av-muted">{t("setup.new_case.intro", { sender })}</p>
        </div>
        <TextLink to="/">{t("setup.cancel")}</TextLink>
      </div>
      <section aria-label={t("setup.new_case.files")} className="av-setup-drops">
        <DropZone title={t("setup.new_case.letter")} icon={ICONS.letter} accept="application/pdf,.pdf,.docx" disabled={busy} onFiles={drop("letter")}>
          {added.letters.length > 0 && <AddedFiles lines={added.letters.map((l) => ({ text: l.name, detail: pageDetail(pages[l.id]) }))} />}
        </DropZone>
        <DropZone title={t("setup.new_case.records")} hint={t("setup.new_case.records_hint")} icon={ICONS.records} multiple disabled={busy} onFiles={drop("records")}>
          {added.records.length > 0 && <AddedFiles lines={[{ text: filesLine(added.records, pages) }]} />}
        </DropZone>
        <DropZone title={t("setup.new_case.other")} hint={t("setup.new_case.other_hint")} icon={ICONS.other} multiple disabled={busy} onFiles={drop("other")}>
          {added.other.length > 0 && <AddedFiles lines={[{ text: filesLine(added.other, pages) }]} />}
        </DropZone>
      </section>
      {made && <FoundInLetter caseId={made} words={words} types={partyTypes} onUse={apply} />}
      <section aria-labelledby="setup-few-questions" className="av-card av-stack">
        <h2 id="setup-few-questions">{t("setup.new_case.few_questions")}</h2>
        {memberships.length > 1 && (
          <Choice
            legend={t("setup.new_case.who_for")}
            hint={t("setup.new_case.who_for_hint")}
            name="organisation"
            value={organisation}
            onChange={setOrganisation}
            options={memberships.map((m) => ({ value: m.organisation_id, label: m.kind === "personal" ? t("setup.new_case.own_practice") : m.name }))}
          />
        )}
        {packs.length > 1 && (
          <Choice
            legend={t("setup.new_case.kind")}
            name="combination"
            value={String(combo)}
            onChange={(v) => setCombo(Number(v))}
            options={packs.map((p, i) => ({ value: String(i), label: t("setup.new_case.kind_option", { case_type: p.case_type.name, specialty: p.specialty.name }) }))}
          />
        )}
        <TextField label={t("setup.case.label")} hint={t("setup.case.label_hint")} name="label" required value={label} onChange={(e) => setLabel(e.target.value)} />
        <div className="av-setup-pair">
          <TextField label={t("setup.instructed.title")} name="instructed_by" value={instructedBy} onChange={(e) => setInstructedBy(e.target.value)} />
          <TextField label={t("setup.new_case.their_firm")} name="firm" value={firm} onChange={(e) => setFirm(e.target.value)} />
        </div>
        {Object.keys(words).length > 0 && <Choice legend={t("setup.case.side")} name="side" value={side} onChange={setSide} options={sideOptions(words)} />}
        <div className="av-setup-pair">
          <TextField label={String(vocabulary.other_side_prompt ?? "")} hint={String(vocabulary.other_side_example ?? "")} name="against" value={against} onChange={(e) => setAgainst(e.target.value)} />
          {statuses.length > 0 && (
            <Choice
              legend={t("setup.new_case.subject_is")}
              hint={vocabulary.subject_status_hint ? String(vocabulary.subject_status_hint) : undefined}
              name="subject_status"
              value={subject}
              onChange={setSubject}
              options={statuses.map(([value, word]) => ({ value, label: humanise(word) }))}
            />
          )}
        </div>
        <div className="av-setup-pair">
          <TextField label={t("setup.case.due")} name="due_date" type="date" value={due} onChange={(e) => setDue(e.target.value)} />
          {place && place.court_tracks.length > 0 && (
            <Choice legend={t("setup.case.track")} name="track" value={track} onChange={setTrack} options={place.court_tracks.map((k) => ({ value: k, label: trackLabel(k) }))} />
          )}
        </div>
        <TextArea label={t("setup.oral.title")} hint={sw("new_case.oral_hint")} rows={3} value={oral} onChange={(e) => setOral(e.target.value)} />
      </section>
      {error && (
        <Notice tone="error">
          <p>{error}</p>
        </Notice>
      )}
      <div className="av-row av-row--spread av-setup-footer">
        <Button variant="quiet" disabled={busy} onClick={later}>
          {t("setup.new_case.later")}
        </Button>
        <Button disabled={busy} onClick={prepare}>
          {t("setup.new_case.prepare")}
        </Button>
      </div>
    </div>
  );
}

const pageDetail = (n: number | null | undefined) => (n == null ? undefined : pagesText(n));

/** "6 files · 1,268 pages" when every file's pages are known, else just "6 files". */
function filesLine(ids: string[], pages: Pages): string {
  const files = t("setup.new_case.files_count", { count: ids.length });
  const counts = ids.map((id) => pages[id]);
  if (counts.some((n) => n == null)) return files;
  return t("setup.new_case.files_pages", { files, pages: pagesText(counts.reduce<number>((sum, n) => sum + (n ?? 0), 0)) });
}

function AddedFiles({ lines }: { lines: { text: string; detail?: string | undefined }[] }) {
  return (
    <ul className="av-setup-plain av-setup-added">
      {lines.map((line) => (
        <li key={line.text}>
          <strong className="av-setup-confirmed">{t("setup.new_case.added")}</strong> <span>{line.text}</span>
          {line.detail && <span className="av-muted"> {line.detail}</span>}
        </li>
      ))}
    </ul>
  );
}

type Form = { label: string; due_date: string; party_role: string | null; court_track: string | null; instructing_party_type: string | null };

const formOf = (c: Schemas["Case"]): Form => ({
  label: c.label,
  due_date: c.due_date ?? "",
  party_role: c.party_role,
  court_track: c.court_track,
  instructing_party_type: c.instructing_party_type,
});

export function CaseDetails() {
  const { caseId = "" } = useParams();
  const path = { params: { path: { case_id: caseId } } };
  const found = useResource(async () => {
    const [item, pack] = await Promise.all([withEtag(api.GET("/v1/cases/{case_id}", path)), unwrap(api.GET("/v1/cases/{case_id}/pack", path))]);
    const place = await unwrap(api.GET("/v1/jurisdictions/{code}", { params: { path: { code: item.data.jurisdiction } } }));
    return { item, pack, place };
  }, [caseId]);
  if (found.error) return <div className="av-page"><LoadError onRetry={found.reload} /></div>;
  if (!found.data) return <div className="av-page"><Loading /></div>;
  return <DetailsView caseId={caseId} {...found.data} />;
}

interface DetailsProps {
  caseId: string;
  item: { data: Schemas["Case"]; etag: string };
  pack: Schemas["CasePack"];
  place: Schemas["JurisdictionSummary"];
}

function DetailsView({ caseId, item, pack, place }: DetailsProps) {
  const [saved, setSaved] = useState(item);
  const [form, setForm] = useState<Form>(formOf(item.data));
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [instructingName, setInstructingName] = useState("");
  const words = partyWords(pack);
  const types = Object.fromEntries(pack.instructing_party_types.map((p) => [p.key!, p.label!]));

  const apply = (s: Schemas["Suggestion"]) => {
    if (s.field === "instructing_party_name") setInstructingName(s.value);
    else setForm((f) => ({ ...f, [s.field]: s.value }));
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setMessage(null);
    const body: Schemas["CaseUpdate"] = { ...form, label: form.label.trim(), due_date: form.due_date || null };
    try {
      const next = await withEtag(api.PATCH("/v1/cases/{case_id}", { params: { path: { case_id: caseId } }, body, headers: ifMatch(saved.etag) }));
      setSaved(next);
      setMessage({ tone: "success", text: t("setup.saved") });
    } catch (err) {
      setMessage({ tone: "error", text: errorMessage(err, { stale: "setup.stale", invalid: "setup.case.invalid" }) });
    }
  };

  return (
    <div className="av-page av-stack">
      <div>
        <h2>{t("setup.details.title")}</h2>
        <p className="av-muted">{t("setup.details.intro")}</p>
      </div>
      <Letter caseId={caseId} />
      <FoundInLetter caseId={caseId} words={words} types={types} onUse={apply} />
      <Section id="setup-case-details" title={t("setup.details.case")}>
        <form className="av-stack" onSubmit={(e) => void save(e)}>
          <TextField label={t("setup.case.label")} name="label" required value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} />
          <Choice
            legend={t("setup.case.side")}
            name="party_role"
            value={form.party_role}
            onChange={(v) => setForm({ ...form, party_role: v })}
            options={pack.party_roles.map((r) => ({ value: r.key!, label: humanise(r.label!) }))}
          />
          {place.court_tracks.length > 0 && (
            <Choice
              legend={t("setup.case.track")}
              name="court_track"
              value={form.court_track}
              onChange={(v) => setForm({ ...form, court_track: v })}
              options={place.court_tracks.map((k) => ({ value: k, label: trackLabel(k) }))}
            />
          )}
          <TextField label={t("setup.case.due")} name="due_date" type="date" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} />
          {message && (
            <Notice tone={message.tone}>
              <p>{message.text}</p>
            </Notice>
          )}
          <div>
            <Button type="submit" variant="secondary">
              {t("setup.details.save")}
            </Button>
          </div>
        </form>
      </Section>
      <InstructedBy caseId={caseId} types={pack.instructing_party_types} name={instructingName} setName={setInstructingName} />
      <SamePerson caseId={caseId} jurisdiction={saved.data.jurisdiction} />
      <OralInstructions caseId={caseId} />
      <div className="av-row av-setup-actions">
        <LinkButton to={`/cases/${caseId}/conflicts`}>{t("setup.details.next")}</LinkButton>
      </div>
    </div>
  );
}

function Letter({ caseId }: { caseId: string }) {
  const path = { params: { path: { case_id: caseId } } };
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/instructions", path)), [caseId]);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const letters = (found.data ?? []).filter((i) => i.document_id);

  const add = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    try {
      const document_id = await uploadFile(caseId, file, "instructions");
      const kind = letters.length > 0 ? "supplementary" : "letter";
      await unwrap(api.POST("/v1/cases/{case_id}/instructions", { ...path, body: { kind, document_id } }));
      await unwrap(api.POST("/v1/cases/{case_id}/prefill", path));
      setReading(true);
      found.reload();
    } catch (err) {
      setError(errorMessage(err, { invalid: "setup.letter.invalid" }));
    }
  };

  return (
    <Section id="setup-letter" title={t("setup.letter.title")} intro={t("setup.letter.intro")}>
      {letters.length > 0 && (
        <ul className="av-rows av-setup-plain">
          {letters.map((l) => (
            <li key={l.id} className="av-row-item">
              <span>{t(l.kind === "letter" ? "setup.letter.first" : "setup.letter.further", { number: l.version_no })}</span>
            </li>
          ))}
        </ul>
      )}
      <FileButton label={letters.length > 0 ? t("setup.letter.add_further") : t("setup.letter.add")} accept="application/pdf,.pdf,.docx" onChange={(e) => void add(e)} />
      {reading && (
        <Notice>
          <p>{t("setup.letter.reading")}</p>
        </Notice>
      )}
      {error && (
        <Notice tone="error">
          <p>{error}</p>
        </Notice>
      )}
    </Section>
  );
}

function FoundInLetter({
  caseId,
  words,
  types,
  onUse,
}: {
  caseId: string;
  words: Record<string, string>;
  types: Record<string, string>;
  onUse: (s: Schemas["Suggestion"]) => void;
}) {
  const path = { params: { path: { case_id: caseId } } };
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/prefill", path)), [caseId]);
  const [done, setDone] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const { reload } = found;
  const reading = found.data?.reading ?? false;
  useEffect(() => {
    if (!reading) return;
    const timer = setInterval(reload, 5000);
    return () => clearInterval(timer);
  }, [reading, reload]);

  const shown = (s: Schemas["Suggestion"]) => {
    if (s.field === "party_role") return packWord(words, s.value);
    if (s.field === "instructing_party_type") return packWord(types, s.value);
    if (s.field === "court_track") return trackLabel(s.value);
    return s.value;
  };
  const decide = async (s: Schemas["Suggestion"], decision: "accepted" | "dismissed") => {
    setError(null);
    try {
      await unwrap(api.PATCH("/v1/cases/{case_id}/prefill/{suggestion_id}", { params: { path: { case_id: caseId, suggestion_id: s.id } }, body: { decision } }));
      if (decision === "accepted") onUse(s);
      setDone((d) => new Set(d).add(s.id));
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  const open = (found.data?.suggestions ?? []).filter((s) => s.status === "suggested" && !done.has(s.id));
  if (!found.data || (!reading && open.length === 0)) return null;
  return (
    <Section id="setup-found" title={t("setup.prefill.title")} intro={t("setup.prefill.intro")}>
      {reading && (
        <Notice>
          <p className="av-row">
            <Spinner />
            {t("setup.letter.reading")}
          </p>
        </Notice>
      )}
      <ul className="av-rows av-setup-plain">
        {open.map((s) => (
          <li key={s.id} className="av-row-item av-row--spread">
            <span className="av-row-item__text">
              <span className="av-muted">{t(`setup.prefill.field.${s.field}`)}</span>
              <strong>{shown(s)}</strong>
            </span>
            <span className="av-row">
              <Button variant="secondary" onClick={() => void decide(s, "accepted")}>
                {t("setup.prefill.use")}
              </Button>
              <Button variant="quiet" onClick={() => void decide(s, "dismissed")}>
                {t("setup.prefill.dismiss")}
              </Button>
            </span>
          </li>
        ))}
      </ul>
      {error && (
        <Notice tone="error">
          <p>{error}</p>
        </Notice>
      )}
    </Section>
  );
}

function InstructedBy({
  caseId,
  types,
  name,
  setName,
}: {
  caseId: string;
  types: Schemas["CasePack"]["instructing_party_types"];
  name: string;
  setName: (v: string) => void;
}) {
  const path = { params: { path: { case_id: caseId } } };
  const found = useResource(() => unwrap(api.GET("/v1/cases/{case_id}/instructing-parties", path)), [caseId]);
  const [adding, setAdding] = useState(false);
  const [typeKey, setTypeKey] = useState(types[0]?.key ?? "");
  const [firm, setFirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const current = found.data?.current ?? [];
  const showForm = adding || (Boolean(found.data) && current.length === 0);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await unwrap(api.POST("/v1/cases/{case_id}/instructing-parties", { ...path, body: { type_key: typeKey, name: name.trim(), firm: firm.trim() } }));
      setName("");
      setFirm("");
      setAdding(false);
      found.reload();
    } catch (err) {
      setError(errorMessage(err, { invalid: "setup.instructed.invalid" }));
    }
  };

  return (
    <Section id="setup-instructed" title={t("setup.instructed.title")}>
      {current.length > 0 && (
        <ul className="av-rows av-setup-plain">
          {current.map((p) => (
            <li key={p.id} className="av-row-item">
              <span>{t("setup.party_line", { name: p.name, firm: p.firm })}</span>
            </li>
          ))}
        </ul>
      )}
      {found.data?.joint && (
        <Notice>
          <p>{t("setup.instructed.joint")}</p>
        </Notice>
      )}
      {showForm ? (
        <form className="av-stack" onSubmit={(e) => void add(e)}>
          {types.length > 1 && (
            <Select
              label={t("setup.instructed.type")}
              value={typeKey}
              onChange={(e) => setTypeKey(e.target.value)}
              options={types.map((p) => ({ value: p.key!, label: humanise(p.label!) }))}
            />
          )}
          <TextField label={t("setup.instructed.name")} name="name" required value={name} onChange={(e) => setName(e.target.value)} />
          <TextField label={t("setup.instructed.firm")} name="firm" required value={firm} onChange={(e) => setFirm(e.target.value)} />
          <div>
            <Button type="submit" variant="secondary">
              {t("setup.instructed.add")}
            </Button>
          </div>
        </form>
      ) : (
        <div>
          <Button variant="quiet" onClick={() => setAdding(true)}>
            {t("setup.instructed.add_another")}
          </Button>
        </div>
      )}
      {error && (
        <Notice tone="error">
          <p>{error}</p>
        </Notice>
      )}
    </Section>
  );
}

function SamePerson({ caseId, jurisdiction }: { caseId: string; jurisdiction: string }) {
  const [identifier, setIdentifier] = useState("");
  const [result, setResult] = useState<Schemas["DuplicateCheck"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const check = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const value = identifier.trim();
      // once this case's own key is saved, leave this case out of the matches
      const body = saved ? { identifier: value, jurisdiction, case_id: caseId } : { identifier: value, jurisdiction };
      setResult(await unwrap(api.POST("/v1/cases/duplicate-check", { body })));
      await unwrap(api.PUT("/v1/cases/{case_id}/subject-key", { params: { path: { case_id: caseId } }, body: { identifier: value } }));
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err, { invalid: "setup.same_person.invalid" }));
    }
  };
  return (
    <Section id="setup-same-person" title={t("setup.same_person.title")} intro={t("setup.same_person.intro")}>
      <form className="av-stack" onSubmit={(e) => void check(e)}>
        <TextField label={t("setup.same_person.identifier")} name="identifier" autoComplete="off" required value={identifier} onChange={(e) => setIdentifier(e.target.value)} />
        <div>
          <Button type="submit" variant="secondary">
            {t("setup.same_person.check")}
          </Button>
        </div>
      </form>
      {result && (
        <Notice tone={result.matches.length ? "warning" : "success"}>
          <p>
            {result.matches.length
              ? t("setup.same_person.found", { label: result.matches.map((m) => m.label).join(", ") })
              : t("setup.same_person.none")}
          </p>
        </Notice>
      )}
      {error && (
        <Notice tone="error">
          <p>{error}</p>
        </Notice>
      )}
    </Section>
  );
}

function OralInstructions({ caseId }: { caseId: string }) {
  const [text, setText] = useState("");
  const [on, setOn] = useState("");
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setMessage(null);
    try {
      await unwrap(api.PUT("/v1/cases/{case_id}/oral-instructions", { params: { path: { case_id: caseId } }, body: { received_on: on, text } }));
      setMessage({ tone: "success", text: t("setup.saved") });
    } catch (err) {
      setMessage({ tone: "error", text: errorMessage(err, { invalid: "setup.oral.invalid" }) });
    }
  };
  return (
    <Section id="setup-oral" title={t("setup.oral.title")} intro={t("setup.oral.intro")}>
      <form className="av-stack" onSubmit={(e) => void save(e)}>
        <DictationBox caseId={caseId} context="oral_instructions" label={t("setup.oral.text")} value={text} onChange={setText} rows={4} />
        <TextField label={t("setup.oral.date")} name="received_on" type="date" required value={on} onChange={(e) => setOn(e.target.value)} />
        {message && (
          <Notice tone={message.tone}>
            <p>{message.text}</p>
          </Notice>
        )}
        <div>
          <Button type="submit" variant="secondary" disabled={!text.trim() || !on}>
            {t("setup.oral.save")}
          </Button>
        </div>
      </form>
    </Section>
  );
}
