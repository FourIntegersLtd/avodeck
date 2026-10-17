// S02 Your expert profile: filled in once and used for every case. About you, the roles you've held (with
// dates, checked against each case), training, your Word template's headings, the declaration (the pack's
// wording is read-only) and your signature, drawn here or uploaded as a photo (rule 4: sent as an image).
import { useEffect, useRef, useState, type ChangeEvent, type FormEvent, type PointerEvent } from "react";
import { humanise, t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, ApiError, ifMatch, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Checkbox, LinkButton, LoadError, Loading, Notice, TextField } from "../../foundation/ui";
import { Balance, FileButton, fileBytes, Section, Select, TextArea, withEtag } from "./shared";

const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const PARTS = 5;

type Template = Schemas["Template"];
type Tagged<T> = { data: T; etag: string };

async function optional<T>(load: () => Promise<T>): Promise<T | null> {
  try {
    return await load();
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

export function Profile() {
  const found = useResource(async () => {
    const [profile, packs, roles, training, template, declaration, signature] = await Promise.all([
      withEtag(api.GET("/v1/me/profile")),
      unwrap(api.GET("/v1/packs")),
      unwrap(api.GET("/v1/me/roles")),
      unwrap(api.GET("/v1/me/training")),
      optional(() => withEtag(api.GET("/v1/me/template"))),
      optional(() => withEtag(api.GET("/v1/me/declaration"))),
      optional(() => unwrap(api.GET("/v1/me/signature/image", { parseAs: "blob" }))),
    ]);
    const code = profile.data.default_jurisdiction ?? packs[0]?.jurisdiction.code;
    const jurisdiction = code ? await optional(() => unwrap(api.GET("/v1/jurisdictions/{code}", { params: { path: { code } } }))) : null;
    return { profile, packs, roles, training, template, declaration, signed: signature !== null, jurisdiction };
  }, []);

  if (found.error) return <div className="av-page"><LoadError onRetry={found.reload} /></div>;
  if (!found.data) return <div className="av-page"><Loading /></div>;
  return <ProfileView {...found.data} />;
}

interface ViewProps {
  profile: Tagged<Schemas["Profile"]>;
  packs: Schemas["PackCombination"][];
  roles: Schemas["Role"][];
  training: Schemas["Training"][];
  template: Tagged<Template> | null;
  declaration: Tagged<Schemas["Declaration"]> | null;
  signed: boolean;
  jurisdiction: Schemas["JurisdictionSummary"] | null;
}

function ProfileView(props: ViewProps) {
  const [roles, setRoles] = useState(props.roles);
  const [training, setTraining] = useState(props.training);
  const [template, setTemplate] = useState(props.template);
  const [signed, setSigned] = useState(props.signed);
  const [about, setAbout] = useState(props.profile.data);
  const aboutDone = Boolean(about.full_name && about.registration_number && about.specialties.length);
  const done = [aboutDone, roles.length > 0, training.length > 0, template !== null, signed].filter(Boolean).length;
  const specialty = about.specialties[0] ?? props.packs[0]?.specialty.code ?? "";
  const words = (props.jurisdiction?.vocabulary.party_roles ?? {}) as Record<string, string>;

  return (
    <div className="av-page av-page--narrow av-stack">
      <div>
        <h1>{t("setup.profile.title")}</h1>
        <p className="av-muted">{t("setup.profile.intro")}</p>
        <p>
          <strong>{t("setup.profile.progress", { done, total: PARTS })}</strong>
        </p>
      </div>
      <AboutYou initial={props.profile} packs={props.packs} onSaved={setAbout} />
      <Roles roles={roles} setRoles={setRoles} specialty={specialty} />
      <Training items={training} setItems={setTraining} />
      <TemplateSection template={template} setTemplate={setTemplate} />
      <DeclarationSection declaration={props.declaration} signed={signed} onSigned={() => setSigned(true)} />
      <Balance words={words} />
      <section aria-labelledby="setup-goes-with-you" className="av-card av-stack">
        <h2 id="setup-goes-with-you">{t("setup.profile.goes_with_you")}</h2>
        <p className="av-muted">{t("setup.profile.goes_with_you_text")}</p>
        <div>
          <LinkButton to="/account" variant="secondary">
            {t("setup.profile.account")}
          </LinkButton>
        </div>
      </section>
    </div>
  );
}

type AboutFields = "full_name" | "profession" | "registration_body" | "registration_number";

function AboutYou({ initial, packs, onSaved }: { initial: Tagged<Schemas["Profile"]>; packs: Schemas["PackCombination"][]; onSaved: (p: Schemas["Profile"]) => void }) {
  const [saved, setSaved] = useState(initial);
  const [form, setForm] = useState(initial.data);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const specialties = [...new Map(packs.map((p) => [p.specialty.code, p.specialty])).values()];
  const field = (name: AboutFields) => ({
    name,
    value: form[name] ?? "",
    onChange: (e: ChangeEvent<HTMLInputElement>) => setForm({ ...form, [name]: e.target.value }),
  });
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setMessage(null);
    const changes: Schemas["ProfileUpdate"] = {};
    for (const key of ["full_name", "profession", "registration_body", "registration_number"] as const) {
      if ((form[key] ?? "") !== (saved.data[key] ?? "")) changes[key] = form[key] || null;
    }
    if (form.specialties.join() !== saved.data.specialties.join()) changes.specialties = form.specialties;
    try {
      const next = await withEtag(api.PATCH("/v1/me/profile", { body: changes, headers: ifMatch(saved.etag) }));
      const merged = { ...form, ...next.data };
      setSaved({ data: merged, etag: next.etag });
      onSaved(merged);
      setMessage({ tone: "success", text: t("setup.saved") });
    } catch (err) {
      setMessage({ tone: "error", text: errorMessage(err, { stale: "setup.stale" }) });
    }
  };
  return (
    <Section id="setup-about" title={t("setup.about.title")}>
      <form className="av-stack" onSubmit={(e) => void save(e)}>
        <TextField label={t("setup.about.full_name")} autoComplete="name" {...field("full_name")} />
        <TextField label={t("setup.about.profession")} {...field("profession")} />
        <TextField label={t("setup.about.registration_body")} {...field("registration_body")} />
        <TextField label={t("setup.about.registration_number")} {...field("registration_number")} />
        {specialties.length > 0 && (
          <fieldset className="av-setup-choice">
            <legend className="av-field__label">{t("setup.about.specialties")}</legend>
            {specialties.map((s) => (
              <Checkbox
                key={s.code}
                label={s.name}
                checked={form.specialties.includes(s.code)}
                onChange={(e) =>
                  setForm({ ...form, specialties: e.target.checked ? [...form.specialties, s.code] : form.specialties.filter((c) => c !== s.code) })
                }
              />
            ))}
          </fieldset>
        )}
        {message && (
          <Notice tone={message.tone}>
            <p>{message.text}</p>
          </Notice>
        )}
        <div>
          <Button type="submit">{t("setup.about.save")}</Button>
        </div>
      </form>
    </Section>
  );
}

const year = (iso: string | null | undefined) => (iso ? iso.slice(0, 4) : t("setup.roles.now"));

function Roles({ roles, setRoles, specialty }: { roles: Schemas["Role"][]; setRoles: (r: Schemas["Role"][]) => void; specialty: string }) {
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ role_title: "", started_on: "", ended_on: "" });
  const [error, setError] = useState<string | null>(null);
  const add = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const role = await unwrap(
        api.POST("/v1/me/roles", { body: { role_title: form.role_title, specialty, started_on: form.started_on, ended_on: form.ended_on || null } }),
      );
      setRoles([role, ...roles]);
      setAdding(false);
      setForm({ role_title: "", started_on: "", ended_on: "" });
    } catch (err) {
      setError(errorMessage(err, { invalid: "setup.roles.invalid" }));
    }
  };
  const remove = async (id: string) => {
    try {
      await unwrap(api.DELETE("/v1/me/roles/{role_id}", { params: { path: { role_id: id } } }));
      setRoles(roles.filter((r) => r.id !== id));
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <Section id="setup-roles" title={t("setup.roles.title")} intro={t("setup.roles.intro")}>
      {roles.length > 0 && (
        <ul className="av-rows av-setup-plain">
          {roles.map((r) => (
            <li key={r.id} className="av-row-item av-row--spread">
              <span className="av-row-item__text">
                <strong>{r.role_title}</strong>
                <span className="av-muted">{t("setup.roles.years", { from: year(r.started_on), to: year(r.ended_on) })}</span>
              </span>
              <Button variant="quiet" onClick={() => void remove(r.id)}>
                {t("setup.remove", { name: r.role_title })}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {error && (
        <Notice tone="error">
          <p>{error}</p>
        </Notice>
      )}
      {adding ? (
        <form className="av-stack" onSubmit={(e) => void add(e)}>
          <TextField label={t("setup.roles.role")} name="role_title" required value={form.role_title} onChange={(e) => setForm({ ...form, role_title: e.target.value })} />
          <TextField label={t("setup.roles.from")} name="started_on" type="date" required value={form.started_on} onChange={(e) => setForm({ ...form, started_on: e.target.value })} />
          <TextField
            label={t("setup.roles.to")}
            hint={t("setup.roles.to_hint")}
            name="ended_on"
            type="date"
            value={form.ended_on}
            onChange={(e) => setForm({ ...form, ended_on: e.target.value })}
          />
          <div className="av-row">
            <Button type="submit">{t("setup.roles.add_this")}</Button>
            <Button variant="quiet" onClick={() => setAdding(false)}>
              {t("setup.cancel")}
            </Button>
          </div>
        </form>
      ) : (
        <div>
          <Button variant="secondary" onClick={() => setAdding(true)}>
            {t("setup.roles.add")}
          </Button>
        </div>
      )}
    </Section>
  );
}

function Training({ items, setItems }: { items: Schemas["Training"][]; setItems: (r: Schemas["Training"][]) => void }) {
  const [form, setForm] = useState({ title: "", year: "" });
  const [error, setError] = useState<string | null>(null);
  const add = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const item = await unwrap(api.POST("/v1/me/training", { body: { title: form.title, year: form.year ? Number(form.year) : null } }));
      setItems([...items, item]);
      setForm({ title: "", year: "" });
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  const remove = async (id: string) => {
    try {
      await unwrap(api.DELETE("/v1/me/training/{training_id}", { params: { path: { training_id: id } } }));
      setItems(items.filter((i) => i.id !== id));
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <Section id="setup-training" title={t("setup.training.title")}>
      {items.length > 0 && (
        <ul className="av-rows av-setup-plain">
          {items.map((i) => (
            <li key={i.id} className="av-row-item av-row--spread">
              <span className="av-row-item__text">
                <strong>{i.title}</strong>
                {i.year && <span className="av-muted">{i.year}</span>}
              </span>
              <Button variant="quiet" onClick={() => void remove(i.id)}>
                {t("setup.remove", { name: i.title })}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {error && (
        <Notice tone="error">
          <p>{error}</p>
        </Notice>
      )}
      <form className="av-stack" onSubmit={(e) => void add(e)}>
        <TextField label={t("setup.training.course")} name="title" required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
        <TextField label={t("setup.training.year")} name="year" inputMode="numeric" value={form.year} onChange={(e) => setForm({ ...form, year: e.target.value })} />
        <div>
          <Button type="submit" variant="secondary">
            {t("setup.training.add")}
          </Button>
        </div>
      </form>
    </Section>
  );
}

const sectionLabel = (key: string) => t(`setup.section.${key}`, {}, humanise(key));

function TemplateSection({ template, setTemplate }: { template: Tagged<Template> | null; setTemplate: (t: Tagged<Template>) => void }) {
  const [mapping, setMapping] = useState<Record<number, string>>({});
  const [suggested, setSuggested] = useState<Set<number>>(new Set());
  const [message, setMessage] = useState<{ tone: "success" | "error" | "info"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setMapping(Object.fromEntries((template?.data.mapping ?? []).map((m) => [m.index, m.section])));
    setSuggested(new Set());
  }, [template]);

  const upload = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setMessage(null);
    try {
      const body = await fileBytes(file);
      const next = await withEtag(
        api.POST("/v1/me/template/file", {
          params: { query: { filename: file.name } },
          body: body as never,
          bodySerializer: (b: unknown) => b as BodyInit,
          headers: { "Content-Type": file.type || DOCX },
        }),
      );
      setTemplate(next);
    } catch (err) {
      setMessage({ tone: "error", text: errorMessage(err, { invalid: "setup.template.not_word" }) });
    }
  };
  const suggest = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const found = await unwrap(api.POST("/v1/me/template/suggestions"));
      setMapping((m) => ({ ...m, ...Object.fromEntries(found.suggestions.map((s) => [s.index, s.section])) }));
      setSuggested(new Set(found.suggestions.map((s) => s.index)));
      setMessage({ tone: "info", text: t("setup.template.suggested_note") });
    } catch (err) {
      setMessage({ tone: "error", text: errorMessage(err, { processing_paused: "setup.template.suggest_paused" }) });
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    if (!template) return;
    setMessage(null);
    const body = {
      mapping: template.data.headings.filter((h) => mapping[h.index]).map((h) => ({ index: h.index, section: mapping[h.index]! })),
    };
    try {
      setTemplate(await withEtag(api.PATCH("/v1/me/template/mapping", { body, headers: ifMatch(template.etag) })));
      setMessage({ tone: "success", text: t("setup.saved") });
    } catch (err) {
      setMessage({ tone: "error", text: errorMessage(err, { stale: "setup.stale" }) });
    }
  };
  const sections = template?.data.sections ?? [];
  const options = [{ value: "", label: t("setup.template.unmapped") }, ...sections.map((key) => ({ value: key, label: sectionLabel(key) }))];
  return (
    <Section id="setup-template" title={t("setup.template.title")} intro={t("setup.template.intro")}>
      {template && (
        <div className="av-row-item">
          <span className="av-row-item__text">
            <strong>{template.data.filename}</strong>
            <span className="av-muted">{t("setup.template.kept")}</span>
          </span>
        </div>
      )}
      <FileButton label={template ? t("setup.template.replace") : t("setup.template.choose")} accept=".docx" onChange={(e) => void upload(e)} />
      {template && template.data.headings.length > 0 && (
        <div className="av-stack">
          <h3>{t("setup.template.headings")}</h3>
          {template.data.headings.map((h) => (
            <div key={h.index} className="av-stack">
              <Select label={h.text} options={options} value={mapping[h.index] ?? ""} onChange={(e) => setMapping({ ...mapping, [h.index]: e.target.value })} />
              {suggested.has(h.index) && <p className="av-small av-muted">{t("setup.template.suggested")}</p>}
            </div>
          ))}
          <div className="av-row">
            <Button variant="secondary" disabled={busy} onClick={() => void suggest()}>
              {t("setup.template.suggest")}
            </Button>
            <Button onClick={() => void save()}>{t("setup.template.save")}</Button>
          </div>
        </div>
      )}
      {message && (
        <Notice tone={message.tone}>
          <p>{message.text}</p>
        </Notice>
      )}
    </Section>
  );
}

function DeclarationSection({ declaration, signed, onSigned }: { declaration: Tagged<Schemas["Declaration"]> | null; signed: boolean; onSigned: () => void }) {
  const [current, setCurrent] = useState(declaration);
  const [additions, setAdditions] = useState<Record<string, string>>(
    Object.fromEntries((declaration?.data.additions ?? []).map((a) => [a.kind, a.text])),
  );
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const save = async () => {
    if (!current) return;
    setMessage(null);
    const body = {
      jurisdiction: current.data.jurisdiction,
      additions: Object.entries(additions)
        .filter(([, text]) => text.trim())
        .map(([kind, text]) => ({ kind, text })),
    };
    try {
      setCurrent(await withEtag(api.PUT("/v1/me/declaration", { body, headers: ifMatch(current.etag) })));
      setMessage({ tone: "success", text: t("setup.saved") });
    } catch (err) {
      setMessage({ tone: "error", text: errorMessage(err, { stale: "setup.stale", locked: "setup.declaration.locked" }) });
    }
  };
  return (
    <Section id="setup-declaration" title={t("setup.declaration.title")}>
      {current?.data.wording.map((w) => (
        <div key={w.key} className="av-stack av-setup-wording">
          <h3>{humanise(w.key)}</h3>
          <p className="av-muted">{t("setup.declaration.fixed")}</p>
          <div className="av-setup-quote">
            {w.text.split("\n\n").map((para, i) => (
              <p key={i}>{para}</p>
            ))}
          </div>
          {current.data.locked ? (
            additions[w.key] && <p>{additions[w.key]}</p>
          ) : (
            <TextArea
              label={t("setup.declaration.addition")}
              hint={t("setup.declaration.addition_hint")}
              rows={3}
              value={additions[w.key] ?? ""}
              onChange={(e) => setAdditions({ ...additions, [w.key]: e.target.value })}
            />
          )}
        </div>
      ))}
      {current && current.data.locked && (
        <Notice>
          <p>{t("setup.declaration.locked")}</p>
        </Notice>
      )}
      {current && !current.data.locked && (
        <div>
          <Button variant="secondary" onClick={() => void save()}>
            {t("setup.declaration.save")}
          </Button>
        </div>
      )}
      {message && (
        <Notice tone={message.tone}>
          <p>{message.text}</p>
        </Notice>
      )}
      <SignaturePad signed={signed} onSigned={onSigned} />
    </Section>
  );
}

function SignaturePad({ signed, onSigned }: { signed: boolean; onSigned: () => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);
  const [drawn, setDrawn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const context = () => canvas.current?.getContext?.("2d") ?? null;

  const point = (e: PointerEvent<HTMLCanvasElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const scale = e.currentTarget.width / (box.width || e.currentTarget.width);
    return { x: (e.clientX - box.left) * scale, y: (e.clientY - box.top) * scale };
  };
  const down = (e: PointerEvent<HTMLCanvasElement>) => {
    const ctx = context();
    if (!ctx) return;
    drawing.current = true;
    const { x, y } = point(e);
    ctx.lineWidth = 3;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(x, y);
  };
  const move = (e: PointerEvent<HTMLCanvasElement>) => {
    const ctx = context();
    if (!drawing.current || !ctx) return;
    const { x, y } = point(e);
    ctx.lineTo(x, y);
    ctx.stroke();
    setDrawn(true);
  };
  const clear = () => {
    const ctx = context();
    if (ctx && canvas.current) ctx.clearRect(0, 0, canvas.current.width, canvas.current.height);
    setDrawn(false);
  };
  const send = async (body: Blob, method: "drawn" | "photo") => {
    setError(null);
    try {
      await unwrap(
        api.POST("/v1/me/signature", {
          params: { query: { method } },
          body: (await fileBytes(body)) as never,
          bodySerializer: (b: unknown) => b as BodyInit,
          headers: { "Content-Type": body.type || "image/png" },
        }),
      );
      onSigned();
    } catch (err) {
      setError(errorMessage(err, { invalid: "setup.signature.not_picture" }));
    }
  };
  const saveDrawn = () => canvas.current?.toBlob((blob) => blob && void send(blob, "drawn"), "image/png");

  return (
    <div className="av-stack">
      <h3>{t("setup.signature.title")}</h3>
      {signed ? (
        <Notice tone="success">
          <p>{t("setup.signature.saved")}</p>
        </Notice>
      ) : (
        <p className="av-muted">{t("setup.signature.intro")}</p>
      )}
      <canvas
        ref={canvas}
        className="av-setup-signature"
        width={600}
        height={200}
        role="img"
        aria-label={t("setup.signature.pad")}
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={() => (drawing.current = false)}
        onPointerLeave={() => (drawing.current = false)}
      />
      <div className="av-row">
        <Button variant="secondary" disabled={!drawn} onClick={saveDrawn}>
          {t("setup.signature.save")}
        </Button>
        <Button variant="quiet" onClick={clear}>
          {t("setup.signature.clear")}
        </Button>
      </div>
      <FileButton
        label={t("setup.signature.photo")}
        accept="image/png,image/jpeg"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void send(file, "photo");
        }}
      />
      {error && (
        <Notice tone="error">
          <p>{error}</p>
        </Notice>
      )}
    </div>
  );
}
