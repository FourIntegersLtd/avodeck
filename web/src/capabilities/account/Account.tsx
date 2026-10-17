// S28 Account and security (foundation part): email, password, the code, backup codes and devices.
// Organisations, notifications and closing the account belong to slice O.
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { formatRelative, t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useSession } from "../../foundation/lib/session";
import { useStepUp } from "../../foundation/lib/stepUp";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap } from "../../foundation/platform-client";
import { Button, LoadError, Loading, Notice, TextField } from "../../foundation/ui";

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
    <div className="av-page av-stack">
      <h1>{t("account.title")}</h1>
      {message && (
        <Notice tone={message.tone}>
          <p>{message.text}</p>
        </Notice>
      )}
      <SigningIn email={me.person.email} act={act} />
      <BackupCodes act={act} />
      <Devices act={act} locale={me.person.locale} />
      <div>
        <Button
          variant="secondary"
          onClick={() =>
            void act(async () => {
              await unwrap(api.POST("/v1/auth/sign-out"));
              navigate("/sign-in", { replace: true });
            })
          }
        >
          {t("account.sign_out")}
        </Button>
      </div>
    </div>
  );
}

type Act = (action: () => Promise<unknown>, success?: string) => Promise<unknown>;

function SigningIn({ email, act }: { email: string; act: Act }) {
  const [changing, setChanging] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [sent, setSent] = useState(false);
  const send = async (e: FormEvent) => {
    e.preventDefault();
    await act(async () => {
      await unwrap(api.POST("/v1/me/email-change", { body: { new_email: newEmail } }));
      setSent(true);
      setChanging(false);
    });
  };
  return (
    <section aria-labelledby="signing-in" className="av-card">
      <h2 id="signing-in">{t("account.sign_in.title")}</h2>
      <div className="av-rows">
        <div className="av-row-item">
          <div>
            <p className="av-field__label">{t("account.email.label")}</p>
            <p>{email}</p>
          </div>
          {!changing && (
            <Button variant="secondary" onClick={() => setChanging(true)}>
              {t("account.email.change")}
            </Button>
          )}
        </div>
        {changing && (
          <form className="av-stack" onSubmit={(e) => void send(e)} noValidate>
            <TextField label={t("account.email.new")} name="new_email" type="email" autoComplete="email" required value={newEmail} onChange={(e) => setNewEmail(e.target.value)} />
            <Button type="submit">{t("account.email.send")}</Button>
          </form>
        )}
        {sent && (
          <Notice tone="success">
            <p>{t("account.email.sent")}</p>
          </Notice>
        )}
        <div className="av-row-item">
          <p className="av-field__label">{t("account.password.label")}</p>
          <Button variant="secondary" onClick={() => void act(() => unwrap(api.POST("/v1/auth/password/forgot", { body: { email } })), "account.password.sent")}>
            {t("account.password.change")}
          </Button>
        </div>
        <div className="av-row-item">
          <div>
            <p className="av-field__label">{t("account.code.label")}</p>
            <p>{t("account.code.value")}</p>
            <p className="av-muted">{t("account.code.always_on")}</p>
          </div>
        </div>
      </div>
    </section>
  );
}

function BackupCodes({ act }: { act: Act }) {
  const [codes, setCodes] = useState<string[] | null>(null);
  return (
    <section aria-labelledby="backup-codes" className="av-card av-stack">
      <h2 id="backup-codes">{t("account.backup.title")}</h2>
      <p>{t("account.backup.intro")}</p>
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
      <div>
        <Button
          variant="secondary"
          onClick={() =>
            void act(async () => {
              const made = await unwrap(api.POST("/v1/auth/mfa/recovery-codes"));
              setCodes(made.recovery_codes);
              return made;
            })
          }
        >
          {t("account.backup.make")}
        </Button>
      </div>
    </section>
  );
}

function Devices({ act, locale }: { act: Act; locale: string | null | undefined }) {
  const devices = useResource(() => unwrap(api.GET("/v1/me/sessions")), []);
  const signOut = (id: string) =>
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
    <section aria-labelledby="devices" className="av-card av-stack">
      <h2 id="devices">{t("account.devices.title")}</h2>
      {devices.error && <LoadError onRetry={devices.reload} />}
      {!devices.data && !devices.error && <Loading />}
      {devices.data && (
        <ul className="av-rows" role="list">
          {devices.data.map((d) => (
            <li key={d.id} className="av-row-item">
              <div>
                <p className="av-field__label">{d.device_label}</p>
                <p className="av-muted">{d.current ? t("account.devices.this_device") : formatRelative(new Date(d.last_seen_at), locale)}</p>
              </div>
              {!d.current && (
                <Button variant="secondary" onClick={() => void signOut(d.id)}>
                  {t("account.devices.sign_out_one", { device: d.device_label })}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {devices.data && devices.data.some((d) => !d.current) && (
        <div>
          <Button variant="danger" onClick={() => void signOutOthers()}>
            {t("account.devices.sign_out_others")}
          </Button>
        </div>
      )}
    </section>
  );
}
