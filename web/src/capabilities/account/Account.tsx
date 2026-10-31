// S28 Account and security (foundation part): email, password, the code, backup codes and devices.
// "How we tell you things" is the foundation's too; organisations and closing the account belong to slice O.
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { formatRelative, humanise, t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useSession } from "../../foundation/lib/session";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { hasScreen, screenPath } from "../../foundation/lib/screens";
import { accountSections } from "../../foundation/lib/slots";
import { BackLink, Button, Checkbox, LoadError, Loading, Notice, SectionIcon, TextField, TextLink, useBusy } from "../../foundation/ui";

export function Account() {
  const { me } = useSession();
  const stepUp = useStepUp();
  const navigate = useNavigate();
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const act = async (action: () => Promise<unknown>, success?: string) => {
    setMessage(null);
    try {
      const result = await stepUp(action);
      if (result !== undefined || success) setMessage(success ? { tone: "success", text: t(success) } : null);
      return result;
    } catch (err) {
      setMessage({ tone: "error", text: errorMessage(err) });
      return undefined;
    }
  };

  return (
    <div className="av-page av-account av-stack">
      <div>
        <BackLink to={screenPath("profile") ?? "/today"}>{t("app.nav.profile")}</BackLink>
        <h1>{t("account.title")}</h1>
        <p className="av-muted">{t("account.intro")}</p>
      </div>
      {message && (
        <Notice tone={message.tone}>
          <p>{message.text}</p>
        </Notice>
      )}
      <SigningIn email={me.person.email} passwordChangedAt={me.person.password_changed_at} locale={me.person.locale} act={act} />
      <Devices
        act={act}
        locale={me.person.locale}
        signOut={() =>
          act(async () => {
            await unwrap(api.POST("/v1/auth/sign-out"));
            navigate("/sign-in", { replace: true });
          })
        }
      />
      <Organisations memberships={me.memberships} />
      <HowWeTellYou />
      {accountSections().map(({ key, titleKey, component: Section }) => (
        <section key={key} aria-labelledby={`account-section-${key}`} className="av-card av-account-card">
          <h2 id={`account-section-${key}`}>{t(titleKey)}</h2>
          <Section />
        </section>
      ))}
    </div>
  );
}

/** "Your organisations": each one you belong to, your role and how many people are in it. Settings and Leave open slice O's screens. */
function Organisations({ memberships }: { memberships: Schemas["Membership"][] }) {
  if (memberships.length === 0) return null;
  const open = (key: string, m: Schemas["Membership"]) =>
    hasScreen(key) ? screenPath(key, { params: { organisation_id: m.organisation_id } }) : null;
  return (
    <section aria-labelledby="your-organisations" className="av-card av-account-card">
      <SectionIcon glyph="people" />
      <h2 id="your-organisations">{t("account.orgs.title")}</h2>
      <ul className="av-rows" role="list">
        {memberships.map((m) => {
          const name = m.kind === "personal" ? t("today.filter.personal") : m.name;
          const people = m.member_count === 1 ? t("account.orgs.just_you") : t("account.orgs.people", { count: m.member_count });
          const settings = open("organisation_settings", m);
          const leave = m.kind === "personal" ? null : open("organisation_leave", m);
          return (
            <li key={m.organisation_id} className="av-row-item">
              <div className="av-row-item__text">
                <p className="av-field__label">{name}</p>
                <p className="av-muted">{t("account.orgs.line", { role: t(`account.orgs.role.${m.role}`, {}, humanise(m.role)), people })}</p>
              </div>
              {settings && (
                <TextLink to={settings} aria-label={t("account.orgs.settings_for", { name })}>
                  {t("account.orgs.settings")}
                </TextLink>
              )}
              {leave && (
                <TextLink to={leave} aria-label={t("account.orgs.leave_name", { name })}>
                  {t("account.orgs.leave")}
                </TextLink>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

type Act = (action: () => Promise<unknown>, success?: string) => Promise<unknown>;

function SigningIn({ email, passwordChangedAt, locale, act }: { email: string; passwordChangedAt?: string | null; locale?: string | null; act: Act }) {
  const [changing, setChanging] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [sent, setSent] = useState(false);
  const { busy, run } = useBusy();
  const send = (e: FormEvent) => {
    e.preventDefault();
    return run(() => act(async () => {
      await unwrap(api.POST("/v1/me/email-change", { body: { new_email: newEmail } }));
      setSent(true);
      setChanging(false);
    }));
  };
  return (
    <section aria-labelledby="signing-in" className="av-card av-account-card">
      <SectionIcon glyph="lock" />
      <h2 id="signing-in">{t("account.sign_in.title")}</h2>
      <div className="av-rows">
        <div className="av-row-item">
          <div className="av-row-item__text">
            <p className="av-field__label">{t("account.email.label")}</p>
            <p className="av-muted">{email}</p>
          </div>
          {!changing && (
            <Button variant="quiet" aria-label={t("account.email.change")} onClick={() => setChanging(true)}>
              {t("account.change")}
            </Button>
          )}
        </div>
        {changing && (
          <form className="av-stack" onSubmit={(e) => void send(e)} noValidate>
            <TextField label={t("account.email.new")} name="new_email" type="email" autoComplete="email" required value={newEmail} onChange={(e) => setNewEmail(e.target.value)} />
            <Button type="submit" busy={busy}>{t("account.email.send")}</Button>
            <Button variant="quiet" onClick={() => setChanging(false)}>
              {t("account.email.cancel")}
            </Button>
          </form>
        )}
        {sent && (
          <Notice tone="success">
            <p>{t("account.email.sent")}</p>
          </Notice>
        )}
        <div className="av-row-item">
          <div className="av-row-item__text">
            <p className="av-field__label">{t("account.password.label")}</p>
            {passwordChangedAt && <p className="av-muted">{t("account.password.changed", { when: formatRelative(new Date(passwordChangedAt), locale) })}</p>}
          </div>
          <Button
            variant="quiet"
            aria-label={t("account.password.change")}
            onClick={() => act(() => unwrap(api.POST("/v1/auth/password/forgot", { body: { email } })), "account.password.sent")}
          >
            {t("account.change")}
          </Button>
        </div>
        <div className="av-row-item">
          <div className="av-row-item__text">
            <p className="av-field__label">{t("account.code.value")}</p>
            <p className="av-muted">{t("account.code.always_on")}</p>
          </div>
          <span className="av-badge av-badge--on">{t("account.code.on")}</span>
        </div>
        <BackupCodes act={act} />
      </div>
      <p className="av-muted av-small">{t("account.sign_in.note")}</p>
    </section>
  );
}

function BackupCodes({ act }: { act: Act }) {
  const [codes, setCodes] = useState<string[] | null>(null);
  const left = useResource(() => unwrap(api.GET("/v1/auth/mfa/recovery-codes")), [], { cache: "account/Account#BackupCodes:left" });
  return (
    <div className="av-row-item">
      <div className="av-row-item__text">
        <p className="av-field__label">{t("account.backup.title")}</p>
        <p className="av-muted">
          {t("account.backup.intro")}
          {left.data && ` ${t("account.backup.left", { remaining: left.data.remaining, total: left.data.total })}`} {t("account.backup.keep")}
        </p>
      </div>
      {codes && (
        <Notice tone="success">
          <p>{t("account.backup.made")}</p>
          <ul className="av-codes">
            {codes.map((code) => (
              <li key={code}>{code}</li>
            ))}
          </ul>
        </Notice>
      )}
      <Button
          variant="secondary"
          className="av-button--small"
          onClick={() =>
            act(async () => {
              const made = await unwrap(api.POST("/v1/auth/mfa/recovery-codes"));
              setCodes(made.recovery_codes);
              left.reload();
              return made;
            })
          }
        >
          {t("account.backup.make")}
        </Button>
    </div>
  );
}

function Devices({ act, locale, signOut }: { act: Act; locale: string | null | undefined; signOut: () => void }) {
  const signOutHere = (
    <Button variant="quiet" onClick={signOut}>
      {t("account.sign_out")}
    </Button>
  );
  const devices = useResource(() => unwrap(api.GET("/v1/me/sessions")), [], { cache: "account/Account#Devices:devices" });
  const signOutOne = (id: string) =>
    act(async () => {
      await unwrap(api.DELETE("/v1/me/sessions/{session_id}", { params: { path: { session_id: id } } }));
      devices.reload();
    });
  const signOutOthers = () =>
    act(async () => {
      await unwrap(api.POST("/v1/me/sessions/revoke-all"));
      devices.reload();
    }, "account.devices.signed_out_others");
  return (
    <section aria-labelledby="devices" className="av-card av-account-card">
      <div className="av-row-item av-row-item--head">
        <h2 id="devices">{t("account.devices.title")}</h2>
        {devices.data && devices.data.some((d) => !d.current) && (
          <Button variant="secondary" className="av-button--small" onClick={() => signOutOthers()}>
            {t("account.devices.sign_out_others")}
          </Button>
        )}
      </div>
      {devices.error && <LoadError onRetry={devices.reload} />}
      {!devices.data && !devices.error && <Loading inline />}
      {/* Signing out of this device stays reachable while the list can't be shown. */}
      {(devices.error || devices.data?.every((d) => !d.current)) && <div>{signOutHere}</div>}
      {devices.data && (
        <ul className="av-rows" role="list">
          {devices.data.map((d) => (
            <li key={d.id} className="av-row-item">
              <div className="av-row-item__text">
                <p className="av-field__label">{d.device_label}</p>
                <p className="av-muted">{d.current ? t("account.devices.this_device") : formatRelative(new Date(d.last_seen_at), locale)}</p>
              </div>
              {d.current ? (
                signOutHere
              ) : (
                <Button variant="quiet" aria-label={t("account.devices.sign_out_one", { device: d.device_label })} onClick={() => signOutOne(d.id)}>
                  {t("account.sign_out")}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

type Choice = Schemas["NotificationPreference"];
const CHANNELS = [
  ["in_app", "account.notify.in_app"],
  ["email", "account.notify.email"],
  ["digest", "account.notify.digest"],
] as const;

function HowWeTellYou() {
  const found = useResource(() => unwrap(api.GET("/v1/me/notification-preferences")), [], { cache: "account/Account#HowWeTellYou:found" });
  const [saved, setSaved] = useState<Record<string, Choice>>({});
  const [failed, setFailed] = useState<string | null>(null);
  const [saving, setSaving] = useState<Record<string, boolean>>({});
  const change = async (choice: Choice, channel: (typeof CHANNELS)[number][0], on: boolean) => {
    const next = { ...choice, [channel]: on };
    setFailed(null);
    if (saving[choice.organisation_id]) return;
    setSaving((s) => ({ ...s, [choice.organisation_id]: true }));
    setSaved((s) => ({ ...s, [choice.organisation_id]: next }));
    try {
      await unwrap(
        api.PUT("/v1/me/notification-preferences", {
          body: [{ organisation_id: next.organisation_id, in_app: next.in_app, email: next.email, digest: next.digest, muted_kinds: next.muted_kinds }],
        }),
      );
    } catch (err) {
      setSaved((s) => ({ ...s, [choice.organisation_id]: choice }));
      setFailed(errorMessage(err));
    } finally {
      setSaving((s) => ({ ...s, [choice.organisation_id]: false }));
    }
  };
  return (
    <section aria-labelledby="how-we-tell-you" className="av-card av-account-card">
      <h2 id="how-we-tell-you">{t("account.notify.title")}</h2>
      <p className="av-muted">{t("account.notify.intro")}</p>
      {failed && (
        <Notice tone="error">
          <p>{failed}</p>
        </Notice>
      )}
      {found.error && <LoadError onRetry={found.reload} />}
      {!found.data && !found.error && <Loading inline />}
      {found.data?.map((original) => {
        const choice = saved[original.organisation_id] ?? original;
        const name = choice.organisation_kind === "personal" ? t("today.filter.personal") : choice.organisation_name;
        return (
          <fieldset key={choice.organisation_id} className="av-choices av-row-item" aria-busy={saving[choice.organisation_id] ? "true" : undefined}>
            <legend className="av-field__label">{name}</legend>
            <div className="av-row">
              {CHANNELS.map(([channel, label]) => (
                <Checkbox key={channel} label={t(label)} checked={choice[channel]} disabled={Boolean(saving[choice.organisation_id])} onChange={(e) => void change(choice, channel, e.target.checked)} />
              ))}
            </div>
          </fieldset>
        );
      })}
    </section>
  );
}
