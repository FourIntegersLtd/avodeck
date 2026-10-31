// S02 Your expert profile: filled in once and used for every case. About you, the roles you've held (with
// dates, checked against each case), training, your Word template's headings, the declaration (the pack's
// wording is read-only) and your signature, drawn here or uploaded as a photo (rule 4: sent as an image).
import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type PointerEvent,
} from "react";
import { humanise, t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import {
  api,
  ApiError,
  ifMatch,
  unwrap,
  type Schemas,
} from "../../foundation/platform-client";
import {
  Button,
  Checkbox,
  LinkButton,
  LoadError,
  Loading,
  Notice,
  ProgressRing,
  StaleChoice,
  TextField,
  useBusy,
} from "../../foundation/ui";
import { sw } from "./words";
import {
  Balance,
  EmptyBox,
  FileButton,
  fileBytes,
  LinkAction,
  PartMark,
  Section,
  Select,
  TextArea,
  versionTag,
  withEtag,
} from "./shared";

const DOCX =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
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
    const [profile, packs, roles, training, template, declaration, signature] =
      await Promise.all([
        withEtag(api.GET("/v1/me/profile")),
        unwrap(api.GET("/v1/packs")),
        unwrap(api.GET("/v1/me/roles")),
        unwrap(api.GET("/v1/me/training")),
        optional(() => withEtag(api.GET("/v1/me/template"))),
        optional(() => withEtag(api.GET("/v1/me/declaration"))),
        optional(() =>
          unwrap(api.GET("/v1/me/signature/image", { parseAs: "blob" })),
        ),
      ]);
    const code =
      profile.data.default_jurisdiction ?? packs[0]?.jurisdiction.code;
    const jurisdiction = code
      ? await optional(() =>
          unwrap(
            api.GET("/v1/jurisdictions/{code}", { params: { path: { code } } }),
          ),
        )
      : null;
    return {
      profile,
      packs,
      roles,
      training,
      template,
      declaration,
      signed: signature !== null,
      jurisdiction,
    };
  }, []);

  if (found.error)
    return (
      <div className="av-page">
        <LoadError onRetry={found.reload} />
      </div>
    );
  if (!found.data)
    return (
      <div className="av-page">
        <Loading />
      </div>
    );
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

const STEPS = ["about", "roles", "training", "template", "signature"] as const;

function ProfileView(props: ViewProps) {
  const [roles, setRoles] = useState(props.roles);
  const [training, setTraining] = useState(props.training);
  const [template, setTemplate] = useState(props.template);
  const [signed, setSigned] = useState(props.signed);
  const [about, setAbout] = useState(props.profile.data);
  const [savedSomething, setSavedSomething] = useState(false);
  const [balanceOpen, setBalanceOpen] = useState(false);
  const noteSaved = () => setSavedSomething(true);
  // The server says which steps are still to do; ask again whenever something on this page changes.
  const [missing, setMissing] = useState<readonly string[]>(
    props.profile.data.missing_steps,
  );
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    let live = true;
    unwrap(api.GET("/v1/me/profile"))
      .then((p) => live && setMissing(p.missing_steps))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [about, roles, training, template, signed]);
  const doneStep = (step: (typeof STEPS)[number]) => !missing.includes(step);
  const aboutDone = doneStep("about");
  const done = STEPS.length - missing.length;
  const firstOpen = STEPS.findIndex((step) => missing.includes(step));
  const next = missing[0] ? t(`setup.profile.step.${missing[0]}`) : "";
  const specialty =
    about.specialties[0] ?? props.packs[0]?.specialty.code ?? "";
  const words = (props.jurisdiction?.vocabulary.party_roles ?? {}) as Record<
    string,
    string
  >;

  return (
    <div className="av-page av-page--wide av-stack">
      <div className="av-row av-row--spread av-setup-title">
        <div className="av-setup-title__text">
          <h1>{t("setup.profile.title")}</h1>
          <p className="av-muted">{t("setup.profile.intro")}</p>
        </div>
        <div className="av-card av-row av-setup-progress">
          <ProgressRing
            value={done}
            max={PARTS}
            size={48}
            label={t("setup.profile.progress", { done, total: PARTS })}
          />
          <div>
            <strong>
              {t("setup.profile.done_count", { done, total: PARTS })}
            </strong>
            <p className="av-muted">
              {missing.length === 0
                ? t("setup.profile.all_done")
                : missing.length === 1
                  ? t("setup.profile.to_finish", { step: capital(next) })
                  : t("setup.profile.next_up", { step: next })}
            </p>
          </div>
        </div>
      </div>
      <Notice>
        <p>{t("setup.profile.saves_note")}</p>
      </Notice>
      <div className="av-setup-pair av-setup-pair--top">
        <AboutYou
          current={firstOpen === 0}
          initial={props.profile}
          packs={props.packs}
          done={aboutDone}
          onSaved={(p) => {
            setAbout(p);
            noteSaved();
          }}
        />
        <Roles
          current={firstOpen === 1}
          roles={roles}
          setRoles={setRoles}
          specialty={specialty}
          onSaved={noteSaved}
        />
      </div>
      <div className="av-setup-pair av-setup-pair--top">
        <Training
          current={firstOpen === 2}
          items={training}
          setItems={setTraining}
          onSaved={noteSaved}
        />
        <TemplateSection
          current={firstOpen === 3}
          template={template}
          setTemplate={setTemplate}
          onSaved={noteSaved}
        />
      </div>
      <div className="av-setup-pair av-setup-pair--top">
        <DeclarationSection
          current={firstOpen === 4}
          declaration={props.declaration}
          signed={signed}
          onSigned={() => {
            setSigned(true);
            noteSaved();
          }}
          onSaved={noteSaved}
        />
        <section
          aria-labelledby="setup-goes-with-you"
          className="av-card av-stack"
        >
          <div className="av-row av-row--spread">
            <div>
              <h2 id="setup-goes-with-you">
                {t("setup.profile.goes_with_you")}
              </h2>
              <p className="av-muted">
                {t("setup.profile.goes_with_you_text")}
              </p>
            </div>
            <LinkButton to="/account" variant="secondary">
              {t("setup.profile.account")}
            </LinkButton>
          </div>
          <div>
            <LinkAction
              aria-expanded={balanceOpen}
              onClick={() => setBalanceOpen(!balanceOpen)}
            >
              {t("setup.balance.show", { months: 12 })}
            </LinkAction>
          </div>
          {balanceOpen && <Balance words={words} />}
        </section>
      </div>
      <div className="av-row av-setup-actions av-setup-done">
        {savedSomething && (
          <span className="av-setup-savedall">
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="3"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
              focusable="false"
            >
              <path d="M5 12l5 5 9-10" />
            </svg>{" "}
            {t("setup.profile.saved_all")}
          </span>
        )}
        <LinkButton to="/today">{t("setup.profile.done")}</LinkButton>
      </div>
    </div>
  );
}

const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

type AboutFields =
  "full_name" | "profession" | "registration_body" | "registration_number";

function AboutYou({
  current,
  initial,
  packs,
  done,
  onSaved,
}: {
  current: boolean;
  initial: Tagged<Schemas["Profile"]>;
  packs: Schemas["PackCombination"][];
  done: boolean;
  onSaved: (p: Schemas["Profile"]) => void;
}) {
  const [saved, setSaved] = useState(initial);
  const [form, setForm] = useState(initial.data);
  const [editing, setEditing] = useState(!done);
  const [message, setMessage] = useState<{
    tone: "success" | "error";
    text: string;
  } | null>(null);
  const specialties = [
    ...new Map(packs.map((p) => [p.specialty.code, p.specialty])).values(),
  ];
  const field = (name: AboutFields) => ({
    name,
    value: form[name] ?? "",
    onChange: (e: ChangeEvent<HTMLInputElement>) =>
      setForm({ ...form, [name]: e.target.value }),
  });
  const [stale, setStale] = useState<Tagged<Schemas["Profile"]> | null>(null);
  const send = async (base: Tagged<Schemas["Profile"]>) => {
    setMessage(null);
    const changes: Schemas["ProfileUpdate"] = {};
    for (const key of [
      "full_name",
      "profession",
      "registration_body",
      "registration_number",
    ] as const) {
      if ((form[key] ?? "") !== (base.data[key] ?? ""))
        changes[key] = form[key] || null;
    }
    if (form.specialties.join() !== base.data.specialties.join())
      changes.specialties = form.specialties;
    try {
      const next = await withEtag(
        api.PATCH("/v1/me/profile", {
          body: changes,
          headers: ifMatch(base.etag),
        }),
      );
      const merged = { ...form, ...next.data };
      setSaved({ data: merged, etag: next.etag });
      setForm(merged);
      onSaved(merged);
      setEditing(false);
      setStale(null);
      setMessage({ tone: "success", text: t("setup.saved") });
    } catch (err) {
      if (err instanceof ApiError && err.code === "stale") {
        // Fetch what is saved now, so the expert can choose; what they typed stays in the form.
        try {
          setStale(await withEtag(api.GET("/v1/me/profile")));
          return;
        } catch {
          /* fall through to the plain message */
        }
      }
      setMessage({ tone: "error", text: errorMessage(err) });
    }
  };
  const sending = useBusy();
  const save = (e: FormEvent) => {
    e.preventDefault();
    void sending.run(() => send(saved));
  };
  const takeSaved = (latest: Tagged<Schemas["Profile"]>) => {
    setSaved(latest);
    setForm(latest.data);
    onSaved(latest.data);
    setStale(null);
  };
  const shown = saved.data;
  const registered = [shown.registration_body, shown.registration_number]
    .filter(Boolean)
    .join(" · ");
  const names = shown.specialties
    .map(
      (code) =>
        specialties.find((s) => s.code === code)?.name ?? humanise(code),
    )
    .join(", ");
  const summary = (label: string, value: string) => (
    <div className="av-field">
      <dt className="av-field__label">{label}</dt>
      <dd className="av-setup-box">{value}</dd>
    </div>
  );
  return (
    <Section
      id="setup-about"
      title={t("setup.about.title")}
      intro={t("setup.about.why")}
      current={current}
      mark={<PartMark done={done} number={1} />}
      action={
        !editing && (
          <LinkAction
            onClick={() => {
              setForm(saved.data);
              setEditing(true);
            }}
          >
            {t("setup.about.edit")}
          </LinkAction>
        )
      }
    >
      {editing ? (
        <form className="av-stack" onSubmit={(e) => void save(e)}>
          <div className="av-setup-two">
            <TextField
              label={t("setup.about.full_name")}
              autoComplete="name"
              {...field("full_name")}
            />
            <TextField
              label={t("setup.about.profession")}
              {...field("profession")}
            />
            <TextField
              label={t("setup.about.registration_body")}
              {...field("registration_body")}
            />
            <TextField
              label={t("setup.about.registration_number")}
              {...field("registration_number")}
            />
          </div>
          {specialties.length > 0 && (
            <fieldset className="av-setup-choice">
              <legend className="av-field__label">
                {t("setup.about.specialties")}
              </legend>
              {specialties.map((s) => (
                <Checkbox
                  key={s.code}
                  label={s.name}
                  checked={form.specialties.includes(s.code)}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      specialties: e.target.checked
                        ? [...form.specialties, s.code]
                        : form.specialties.filter((c) => c !== s.code),
                    })
                  }
                />
              ))}
            </fieldset>
          )}
          <div className="av-row">
            <Button type="submit" busy={sending.busy}>
              {t("setup.about.save")}
            </Button>
            {done && (
              <Button
                variant="quiet"
                onClick={() => {
                  setForm(saved.data);
                  setEditing(false);
                }}
              >
                {t("setup.cancel")}
              </Button>
            )}
          </div>
        </form>
      ) : (
        <dl className="av-setup-summary av-setup-two">
          {summary(t("setup.about.full_name"), shown.full_name ?? "")}
          {summary(t("setup.about.registration_body"), registered)}
          {summary(
            t("setup.about.specialties"),
            names || t("setup.about.specialties_none"),
          )}
        </dl>
      )}
      {message && (
        <Notice tone={message.tone}>
          <p>{message.text}</p>
        </Notice>
      )}
      {stale && (
        <StaleChoice
          draft={form}
          saved={stale.data}
          onKeepMine={() => sending.run(() => send(stale))}
          onUseSaved={() => takeSaved(stale)}
          onClose={() => setStale(null)}
          show={(p) =>
            [
              p.full_name,
              [p.registration_body, p.registration_number]
                .filter(Boolean)
                .join(" · "),
            ]
              .filter(Boolean)
              .join(", ")
          }
        />
      )}
    </Section>
  );
}

const year = (iso: string | null | undefined) =>
  iso ? iso.slice(0, 4) : t("setup.roles.now");

function Roles({
  current,
  roles,
  setRoles,
  specialty,
  onSaved,
}: {
  current: boolean;
  roles: Schemas["Role"][];
  setRoles: (r: Schemas["Role"][]) => void;
  specialty: string;
  onSaved: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({
    role_title: "",
    started_on: "",
    ended_on: "",
  });
  const [error, setError] = useState<string | null>(null);
  const adding_ = useBusy();
  const add = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const role = await unwrap(
        api.POST("/v1/me/roles", {
          body: {
            role_title: form.role_title,
            specialty,
            started_on: form.started_on,
            ended_on: form.ended_on || null,
          },
        }),
      );
      setRoles([role, ...roles]);
      onSaved();
      setAdding(false);
      setForm({ role_title: "", started_on: "", ended_on: "" });
    } catch (err) {
      setError(errorMessage(err, { invalid: "setup.roles.invalid" }));
    }
  };
  const [editing, setEditing] = useState<string | null>(null);
  const [edit, setEdit] = useState({
    role_title: "",
    started_on: "",
    ended_on: "",
  });
  const startEdit = (r: Schemas["Role"]) => {
    setError(null);
    setEditing(r.id);
    setEdit({
      role_title: r.role_title,
      started_on: r.started_on ?? "",
      ended_on: r.ended_on ?? "",
    });
  };
  const [stale, setStale] = useState<Schemas["Role"] | null>(null);
  const send = async (r: Schemas["Role"], version: number) => {
    setError(null);
    try {
      const role = await unwrap(
        api.PATCH("/v1/me/roles/{role_id}", {
          params: { path: { role_id: r.id } },
          headers: ifMatch(versionTag(version)),
          body: {
            role_title: edit.role_title,
            started_on: edit.started_on,
            ended_on: edit.ended_on || null,
            setting: r.setting ?? null,
          },
        }),
      );
      setRoles(roles.map((x) => (x.id === r.id ? role : x)));
      onSaved();
      setEditing(null);
      setStale(null);
    } catch (err) {
      if (err instanceof ApiError && err.code === "stale") {
        try {
          const latest = (await unwrap(api.GET("/v1/me/roles"))).find(
            (x) => x.id === r.id,
          );
          if (latest) {
            setStale(latest);
            return;
          }
        } catch {
          /* fall through to the plain message */
        }
      }
      setError(errorMessage(err, { invalid: "setup.roles.invalid" }));
    }
  };
  const sending = useBusy();
  const saveEdit = (e: FormEvent, r: Schemas["Role"]) => {
    e.preventDefault();
    void sending.run(() => send(r, r.version));
  };
  const takeSavedRole = (latest: Schemas["Role"]) => {
    setRoles(roles.map((x) => (x.id === latest.id ? latest : x)));
    onSaved();
    setEditing(null);
    setStale(null);
  };
  const showRole = (r: {
    role_title: string;
    started_on?: string | null;
    ended_on?: string | null;
  }) => `${r.role_title}, ${year(r.started_on)} – ${year(r.ended_on)}`;
  const remove = async (id: string) => {
    try {
      await unwrap(
        api.DELETE("/v1/me/roles/{role_id}", {
          params: { path: { role_id: id } },
        }),
      );
      setRoles(roles.filter((r) => r.id !== id));
      onSaved();
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <Section
      id="setup-roles"
      current={current}
      title={t("setup.roles.title")}
      intro={t("setup.roles.intro")}
      mark={<PartMark done={roles.length > 0} number={2} />}
    >
      {roles.length > 0 && (
        <ul className="av-setup-lines av-setup-plain">
          {roles.map((r) =>
            editing === r.id ? (
              <li key={r.id} className="av-setup-line">
                <form
                  className="av-stack"
                  onSubmit={(e) => void saveEdit(e, r)}
                >
                  <TextField
                    label={t("setup.roles.edit_role")}
                    name="role_title"
                    required
                    value={edit.role_title}
                    onChange={(e) =>
                      setEdit({ ...edit, role_title: e.target.value })
                    }
                  />
                  <TextField
                    label={t("setup.roles.from")}
                    name="started_on"
                    type="date"
                    required
                    value={edit.started_on}
                    onChange={(e) =>
                      setEdit({ ...edit, started_on: e.target.value })
                    }
                  />
                  <TextField
                    label={t("setup.roles.edit_until")}
                    hint={t("setup.roles.to_hint")}
                    name="ended_on"
                    type="date"
                    value={edit.ended_on}
                    onChange={(e) =>
                      setEdit({ ...edit, ended_on: e.target.value })
                    }
                  />
                  <div className="av-row">
                    <Button type="submit" busy={sending.busy}>
                      {t("setup.roles.save")}
                    </Button>
                    <Button variant="quiet" onClick={() => setEditing(null)}>
                      {t("setup.cancel")}
                    </Button>
                    <Button variant="quiet" onClick={() => remove(r.id)}>
                      {t("setup.remove", { name: r.role_title })}
                    </Button>
                  </div>
                </form>
              </li>
            ) : (
              <li key={r.id} className="av-setup-line">
                <strong className="av-setup-line__title">{r.role_title}</strong>
                <span className="av-mono av-muted">
                  {t("setup.roles.years", {
                    from: year(r.started_on),
                    to: year(r.ended_on),
                  })}
                </span>
                <LinkAction
                  aria-label={t("setup.roles.edit", { name: r.role_title })}
                  onClick={() => startEdit(r)}
                >
                  {t("setup.edit")}
                </LinkAction>
              </li>
            ),
          )}
        </ul>
      )}
      {error && (
        <Notice tone="error">
          <p>{error}</p>
        </Notice>
      )}
      {adding ? (
        <form
          className="av-stack"
          onSubmit={(e) => void adding_.run(() => add(e))}
        >
          <TextField
            label={t("setup.roles.role")}
            name="role_title"
            required
            value={form.role_title}
            onChange={(e) => setForm({ ...form, role_title: e.target.value })}
          />
          <TextField
            label={t("setup.roles.from")}
            name="started_on"
            type="date"
            required
            value={form.started_on}
            onChange={(e) => setForm({ ...form, started_on: e.target.value })}
          />
          <TextField
            label={t("setup.roles.to")}
            hint={t("setup.roles.to_hint")}
            name="ended_on"
            type="date"
            value={form.ended_on}
            onChange={(e) => setForm({ ...form, ended_on: e.target.value })}
          />
          <div className="av-row">
            <Button type="submit" busy={adding_.busy}>
              {t("setup.roles.add_this")}
            </Button>
            <Button variant="quiet" onClick={() => setAdding(false)}>
              {t("setup.cancel")}
            </Button>
          </div>
        </form>
      ) : roles.length === 0 ? (
        <EmptyBox
          title={t("setup.roles.empty_title")}
          text={t("setup.roles.empty_text")}
        >
          <Button variant="secondary" onClick={() => setAdding(true)}>
            {t("setup.roles.add_first")}
          </Button>
        </EmptyBox>
      ) : (
        <div>
          <LinkAction onClick={() => setAdding(true)}>
            <span aria-hidden="true" className="av-setup-plus">
              +
            </span>
            {t("setup.roles.add")}
          </LinkAction>
        </div>
      )}
      {stale && editing && (
        <StaleChoice
          draft={edit}
          saved={stale}
          onKeepMine={() => {
            const mine = roles.find((x) => x.id === editing);
            if (mine) void sending.run(() => send(mine, stale.version));
          }}
          onUseSaved={() => takeSavedRole(stale)}
          onClose={() => setStale(null)}
          show={showRole}
        />
      )}
    </Section>
  );
}

function Training({
  current,
  items,
  setItems,
  onSaved,
}: {
  current: boolean;
  items: Schemas["Training"][];
  setItems: (r: Schemas["Training"][]) => void;
  onSaved: () => void;
}) {
  const [managing, setManaging] = useState(false);
  const [form, setForm] = useState({ title: "", year: "" });
  const [error, setError] = useState<string | null>(null);
  const adding_ = useBusy();
  const add = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const item = await unwrap(
        api.POST("/v1/me/training", {
          body: {
            title: form.title,
            year: form.year ? Number(form.year) : null,
          },
        }),
      );
      setItems([...items, item]);
      onSaved();
      setForm({ title: "", year: "" });
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  const remove = async (id: string) => {
    try {
      await unwrap(
        api.DELETE("/v1/me/training/{training_id}", {
          params: { path: { training_id: id } },
        }),
      );
      setItems(items.filter((i) => i.id !== id));
      onSaved();
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  const [adding, setAdding] = useState(false);
  const open = managing || adding;
  return (
    <Section
      id="setup-training"
      current={current}
      title={t("setup.training.title")}
      intro={t("setup.training.why")}
      mark={<PartMark done={items.length > 0} number={3} />}
    >
      {items.length > 0 && (
        <ul className="av-setup-lines av-setup-plain">
          {items.map((i) => (
            <li key={i.id} className="av-setup-line">
              <span className="av-setup-line__title">{i.title}</span>
              {i.year && <span className="av-mono av-muted">{i.year}</span>}
              {managing && (
                <Button variant="quiet" onClick={() => remove(i.id)}>
                  {t("setup.remove", { name: i.title })}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {error && (
        <Notice tone="error">
          <p>{error}</p>
        </Notice>
      )}
      {items.length === 0 && !adding && (
        <EmptyBox
          title={t("setup.training.empty_title")}
          text={t("setup.training.empty_text")}
        >
          <Button variant="secondary" onClick={() => setAdding(true)}>
            {t("setup.training.add_first")}
          </Button>
        </EmptyBox>
      )}
      {open && (
        <form
          className="av-stack"
          onSubmit={(e) => void adding_.run(() => add(e))}
        >
          <TextField
            label={t("setup.training.course")}
            name="title"
            required
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
          />
          <TextField
            label={t("setup.training.year")}
            name="year"
            inputMode="numeric"
            value={form.year}
            onChange={(e) => setForm({ ...form, year: e.target.value })}
          />
          <div>
            <Button type="submit" variant="secondary" busy={adding_.busy}>
              {t("setup.training.add")}
            </Button>
          </div>
        </form>
      )}
      {items.length > 0 && (
        <div>
          <LinkAction
            aria-expanded={managing}
            onClick={() => setManaging(!managing)}
          >
            {managing
              ? t("setup.training.done_editing")
              : t("setup.training.edit")}
          </LinkAction>
        </div>
      )}
    </Section>
  );
}

const sectionLabel = (key: string) =>
  t(`setup.section.${key}`, {}, humanise(key));

function TemplateSection({
  current,
  template,
  setTemplate,
  onSaved,
}: {
  current: boolean;
  template: Tagged<Template> | null;
  setTemplate: (t: Tagged<Template>) => void;
  onSaved: () => void;
}) {
  const [matching, setMatching] = useState(false);
  const [mapping, setMapping] = useState<Record<number, string>>({});
  const [suggested, setSuggested] = useState<Set<number>>(new Set());
  const [message, setMessage] = useState<{
    tone: "success" | "error" | "info";
    text: string;
  } | null>(null);
  const uploading = useBusy();
  useEffect(() => {
    setMapping(
      Object.fromEntries(
        (template?.data.mapping ?? []).map((m) => [m.index, m.section]),
      ),
    );
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
      onSaved();
    } catch (err) {
      setMessage({
        tone: "error",
        text: errorMessage(err, { invalid: "setup.template.not_word" }),
      });
    }
  };
  const suggest = async () => {
    setMessage(null);
    try {
      const found = await unwrap(api.POST("/v1/me/template/suggestions"));
      setMapping((m) => ({
        ...m,
        ...Object.fromEntries(
          found.suggestions.map((s) => [s.index, s.section]),
        ),
      }));
      setSuggested(new Set(found.suggestions.map((s) => s.index)));
      setMessage({ tone: "info", text: t("setup.template.suggested_note") });
    } catch (err) {
      setMessage({
        tone: "error",
        text: errorMessage(err, {
          processing_paused: "setup.template.suggest_paused",
        }),
      });
    }
  };
  const [stale, setStale] = useState<Tagged<Template> | null>(null);
  const send = async (base: Tagged<Template>) => {
    setMessage(null);
    const body = {
      mapping: base.data.headings
        .filter((h) => mapping[h.index])
        .map((h) => ({ index: h.index, section: mapping[h.index]! })),
    };
    try {
      setStale(null);
      setTemplate(
        await withEtag(
          api.PATCH("/v1/me/template/mapping", {
            body,
            headers: ifMatch(base.etag),
          }),
        ),
      );
      onSaved();
      setMessage({ tone: "success", text: t("setup.saved") });
    } catch (err) {
      if (err instanceof ApiError && err.code === "stale") {
        try {
          setStale(await withEtag(api.GET("/v1/me/template")));
          return;
        } catch {
          /* fall through to the plain message */
        }
      }
      setMessage({ tone: "error", text: errorMessage(err) });
    }
  };
  const save = () => (template ? send(template) : undefined);
  const sections = template?.data.sections ?? [];
  const options = [
    { value: "", label: t("setup.template.unmapped") },
    ...sections.map((key) => ({ value: key, label: sectionLabel(key) })),
  ];
  return (
    <Section
      id="setup-template"
      current={current}
      title={t("setup.template.title")}
      intro={template ? undefined : t("setup.template.intro")}
      mark={<PartMark done={template !== null} number={4} />}
    >
      {template ? (
        <div className="av-setup-filetile">
          <svg
            width="28"
            height="28"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
            focusable="false"
          >
            <path d="M7 3h8l4 4v14H7z" />
            <path d="M10 12h6M10 16h6" />
          </svg>
          <span className="av-setup-filetile__text">
            <strong>{template.data.filename}</strong>
            <span className="av-muted">{t("setup.template.kept")}</span>
          </span>
          <FileButton
            link
            busy={uploading.busy}
            label={t("setup.template.replace")}
            accept=".docx"
            onChange={(e) => void uploading.run(() => upload(e))}
          />
        </div>
      ) : (
        <EmptyBox
          title={t("setup.template.empty_title")}
          text={t("setup.template.empty_text")}
        >
          <FileButton
            busy={uploading.busy}
            label={t("setup.template.choose")}
            accept=".docx"
            onChange={(e) => void uploading.run(() => upload(e))}
          />
        </EmptyBox>
      )}
      {template && template.data.styles.length > 0 && (
        <ul
          className="av-setup-plain av-setup-tiles"
          aria-label={t("setup.template.styles")}
        >
          {template.data.styles.map((style) => {
            const detail = styleLook(
              template.data.style_details?.find((d) => d.name === style),
              t,
            );
            return (
              <li key={style} className="av-setup-tile">
                <span>{style}</span>
                {detail && (
                  <span className="av-setup-tile__detail">{detail}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {template && template.data.headings.length > 0 && (
        <div>
          <LinkAction
            aria-expanded={matching}
            onClick={() => setMatching(!matching)}
          >
            {t("setup.template.match")}
          </LinkAction>
        </div>
      )}
      {template && matching && template.data.headings.length > 0 && (
        <div className="av-stack">
          <h3>{t("setup.template.headings")}</h3>
          {template.data.headings.map((h) => (
            <div key={h.index} className="av-stack">
              <Select
                label={h.text}
                options={options}
                value={mapping[h.index] ?? ""}
                onChange={(e) =>
                  setMapping({ ...mapping, [h.index]: e.target.value })
                }
              />
              {suggested.has(h.index) && (
                <p className="av-small av-muted">
                  {t("setup.template.suggested")}
                </p>
              )}
            </div>
          ))}
          <div className="av-row">
            <Button variant="secondary" onClick={suggest}>
              {t("setup.template.suggest")}
            </Button>
            <Button onClick={save}>{t("setup.template.save")}</Button>
          </div>
        </div>
      )}
      {message && (
        <Notice tone={message.tone}>
          <p>{message.text}</p>
        </Notice>
      )}
      {stale && (
        <StaleChoice
          draft={mapping}
          saved={Object.fromEntries(
            stale.data.mapping.map((m) => [m.index, m.section]),
          )}
          onKeepMine={() => send(stale)}
          onUseSaved={() => {
            setTemplate(stale);
            setStale(null);
          }}
          onClose={() => setStale(null)}
          show={(m) =>
            sw("template.stale_show", {
              count: Object.values(m).filter(Boolean).length,
            })
          }
        />
      )}
    </Section>
  );
}

function DeclarationSection({
  current: isCurrent,
  declaration,
  signed,
  onSigned,
  onSaved,
}: {
  current: boolean;
  declaration: Tagged<Schemas["Declaration"]> | null;
  signed: boolean;
  onSigned: () => void;
  onSaved: () => void;
}) {
  const [reviewing, setReviewing] = useState(false);
  const [current, setCurrent] = useState(declaration);
  const [additions, setAdditions] = useState<Record<string, string>>(
    Object.fromEntries(
      (declaration?.data.additions ?? []).map((a) => [a.kind, a.text]),
    ),
  );
  const [message, setMessage] = useState<{
    tone: "success" | "error";
    text: string;
  } | null>(null);
  const [stale, setStale] = useState<Tagged<Schemas["Declaration"]> | null>(
    null,
  );
  const send = async (base: Tagged<Schemas["Declaration"]>) => {
    setMessage(null);
    const body = {
      jurisdiction: base.data.jurisdiction,
      additions: Object.entries(additions)
        .filter(([, text]) => text.trim())
        .map(([kind, text]) => ({ kind, text })),
    };
    try {
      setCurrent(
        await withEtag(
          api.PUT("/v1/me/declaration", { body, headers: ifMatch(base.etag) }),
        ),
      );
      setStale(null);
      onSaved();
      setMessage({ tone: "success", text: t("setup.saved") });
    } catch (err) {
      if (err instanceof ApiError && err.code === "stale") {
        try {
          setStale(await withEtag(api.GET("/v1/me/declaration")));
          return;
        } catch {
          /* fall through to the plain message */
        }
      }
      setMessage({
        tone: "error",
        text: errorMessage(err, { locked: "setup.declaration.locked" }),
      });
    }
  };
  const save = () => (current ? send(current) : undefined);
  const addedText = (d: Schemas["Declaration"]) =>
    d.additions.map((a) => a.text).join(" / ");
  return (
    <Section
      id="setup-declaration"
      current={isCurrent}
      title={t("setup.declaration.title")}
      intro={t("setup.signature.why")}
      mark={<PartMark done={signed} number={5} />}
    >
      {current && current.data.wording.length > 0 && (
        <div className="av-setup-pair av-setup-pair--top av-setup-halves">
          <div className="av-setup-half av-stack">
            <strong className="av-setup-half__title">
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
                focusable="false"
              >
                <rect x="5" y="11" width="14" height="9" rx="2" />
                <path d="M8 11V8a4 4 0 018 0v3" />
              </svg>{" "}
              {humanise(current.data.wording[0]!.key)}
            </strong>
            <p className="av-muted">{t("setup.declaration.tile_fixed")}</p>
          </div>
          <div className="av-setup-half av-stack">
            <strong className="av-setup-half__title">
              {t("setup.declaration.tile_yours")}
            </strong>
            <p className="av-muted">
              {current.data.wording[0]!.points
                ? t("setup.declaration.tile_points", {
                    count: current.data.wording[0]!.points,
                  })
                : t("setup.declaration.tile_plain")}
            </p>
            <div>
              <LinkAction
                aria-expanded={reviewing}
                aria-controls="declaration-wording"
                onClick={() => setReviewing(!reviewing)}
              >
                {t("setup.declaration.review")}
              </LinkAction>
            </div>
          </div>
        </div>
      )}
      {reviewing &&
        current?.data.wording.map((w, i) => (
          <div
            key={w.key}
            id={i === 0 ? "declaration-wording" : undefined}
            className="av-stack av-setup-wording"
          >
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
                onChange={(e) =>
                  setAdditions({ ...additions, [w.key]: e.target.value })
                }
              />
            )}
          </div>
        ))}
      {current && current.data.locked && (
        <Notice>
          <p>{t("setup.declaration.locked")}</p>
        </Notice>
      )}
      {reviewing && current && !current.data.locked && (
        <div>
          <Button variant="secondary" onClick={save}>
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
      {stale && (
        <StaleChoice
          draft={Object.values(additions)
            .filter((x) => x.trim())
            .join(" / ")}
          saved={addedText(stale.data)}
          onKeepMine={() => send(stale)}
          onUseSaved={() => {
            setCurrent(stale);
            setAdditions(
              Object.fromEntries(
                stale.data.additions.map((a) => [a.kind, a.text]),
              ),
            );
            setStale(null);
          }}
          onClose={() => setStale(null)}
        />
      )}
    </Section>
  );
}

function SignaturePad({
  signed,
  onSigned,
}: {
  signed: boolean;
  onSigned: () => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);
  const [drawn, setDrawn] = useState(false);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sending = useBusy();
  const context = () => canvas.current?.getContext?.("2d") ?? null;

  const point = (e: PointerEvent<HTMLCanvasElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const scale = e.currentTarget.width / (box.width || e.currentTarget.width);
    return {
      x: (e.clientX - box.left) * scale,
      y: (e.clientY - box.top) * scale,
    };
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
    if (ctx && canvas.current)
      ctx.clearRect(0, 0, canvas.current.width, canvas.current.height);
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
  const saveDrawn = () =>
    canvas.current?.toBlob(
      (blob) => blob && void sending.run(() => send(blob, "drawn")),
      "image/png",
    );

  return (
    <div className="av-stack">
      <button
        type="button"
        className="av-setup-sigbar"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <svg
          width="24"
          height="24"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          focusable="false"
        >
          <path d="M4 20h16M14 5l5 5L9 20H4v-5z" />
        </svg>
        {signed ? (
          <span>
            <strong>{t("setup.signature.bar_saved")}</strong>{" "}
            {t("setup.template.replace")}
          </span>
        ) : (
          <span>
            <strong>{t("setup.signature.bar_title")}</strong>{" "}
            {t("setup.signature.bar_text")}
          </span>
        )}
      </button>
      {open && (
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
            <Button
              variant="secondary"
              busy={sending.busy}
              disabled={!drawn}
              onClick={saveDrawn}
            >
              {t("setup.signature.save")}
            </Button>
            <Button variant="quiet" onClick={clear}>
              {t("setup.signature.clear")}
            </Button>
          </div>
          <FileButton
            busy={sending.busy}
            label={t("setup.signature.photo")}
            accept="image/png,image/jpeg"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void sending.run(() => send(file, "photo"));
            }}
          />
        </div>
      )}
      {error && (
        <Notice tone="error">
          <p>{error}</p>
        </Notice>
      )}
    </div>
  );
}

type StyleDetail = NonNullable<Schemas["Template"]["style_details"]>[number];

/** "Bold 14 pt", "Numbered", "Italic · Indented": the server's facts about a style, in words. */
function styleLook(
  d: StyleDetail | undefined,
  t: (key: string, params?: Record<string, string | number>) => string,
): string {
  if (!d) return "";
  const look = [
    d.bold ? t("setup.template.style.bold") : "",
    d.italic ? t("setup.template.style.italic") : "",
    d.size_pt != null
      ? t("setup.template.style.size", { size: d.size_pt })
      : "",
  ]
    .filter(Boolean)
    .join(" ");
  const layout = [
    d.numbered ? t("setup.template.style.numbered") : "",
    d.indented ? t("setup.template.style.indented") : "",
  ].filter(Boolean);
  return [look, ...layout].filter(Boolean).join(" · ");
}
