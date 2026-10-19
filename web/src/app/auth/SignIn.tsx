// S01 Sign in: password, then the 6-digit code. The first time, the expert sets up the code and
// sees backup codes once. A lost phone uses a backup code; a forgotten password gets a link.
import { useEffect, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { t } from "../../foundation/i18n";
import { DevSkipCode, rememberDevEmail } from "../../foundation/lib/DevSkipCode";
import { errorMessage } from "../../foundation/lib/errors";
import { api, unwrap, type Schemas } from "../../foundation/platform-client";
import { Button, Checkbox, CodeField, Loading, Notice, SupportLine, TextField } from "../../foundation/ui";
import { AuthLayout } from "./AuthLayout";

type Step = "password" | "code" | "enrol" | "codes" | "recover" | "forgot";

const CREDENTIAL_ERRORS = { invalid_credentials: "sign_in.error.credentials", invalid: "sign_in.error.invalid" };

export function SignIn() {
  const location = useLocation();
  const navigate = useNavigate();
  const arrived = (location.state ?? {}) as { step?: "code" | "enrol"; from?: string };
  const [step, setStep] = useState<Step>(arrived.step ?? "password");
  const [codes, setCodes] = useState<string[]>([]);

  const go = (next: Step) => setStep(next);
  const finish = () => navigate(arrived.from && arrived.from !== "/sign-in" ? arrived.from : "/today", { replace: true });
  const afterPassword = (result: Schemas["SignInResult"]) => {
    if (result.status === "signed_in") finish(); // the local API skips the code (DEV_SKIP_MFA)
    else go(result.mfa_enrolled ? "code" : "enrol");
  };
  const afterCode = (result: Schemas["Verified"]) => {
    if (result.recovery_codes?.length) {
      setCodes(result.recovery_codes);
      go("codes");
    } else finish();
  };

  return (
    <AuthLayout>
      {step === "password" && <PasswordStep onDone={afterPassword} go={go} />}
      {step === "code" && <CodeStep onDone={afterCode} go={go} />}
      {step === "enrol" && <EnrolStep onDone={afterCode} />}
      {step === "codes" && <BackupCodes codes={codes} onDone={finish} />}
      {step === "recover" && <RecoverStep onDone={afterPassword} go={go} />}
      {step === "forgot" && <ForgotStep go={go} />}
    </AuthLayout>
  );
}

function useSubmit<T>(send: () => Promise<T>, onDone: (result: T) => void, own: Record<string, string> = {}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await send();
      setBusy(false);
      onDone(result);
    } catch (err) {
      setError(errorMessage(err, own));
      setBusy(false);
    }
  };
  return { error, busy, submit: (e: FormEvent) => void submit(e) };
}

function ErrorNotice({ error }: { error: string | null }) {
  return error ? (
    <Notice tone="error">
      <p>{error}</p>
    </Notice>
  ) : null;
}

function PasswordStep({ onDone, go }: { onDone: (r: Schemas["SignInResult"]) => void; go: (s: Step) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const { error, busy, submit } = useSubmit(
    () => {
      rememberDevEmail(email);
      return unwrap(api.POST("/v1/auth/sign-in", { body: { email, password } }));
    },
    onDone,
    CREDENTIAL_ERRORS,
  );
  return (
    <>
      <h1>{t("sign_in.password.title")}</h1>
      <p className="av-muted">{t("sign_in.password.intro")}</p>
      <ErrorNotice error={error} />
      <form className="av-stack" onSubmit={submit} noValidate>
        <TextField label={t("sign_in.email")} name="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <TextField
          label={t("sign_in.password")}
          name="password"
          type={show ? "text" : "password"}
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <div className="av-row av-row--spread">
          <Checkbox label={t("ui.show_password")} checked={show} onChange={(e) => setShow(e.target.checked)} />
          <Button variant="quiet" onClick={() => go("forgot")}>
            {t("sign_in.forgot")}
          </Button>
        </div>
        <Button type="submit" wide disabled={busy}>
          {t("sign_in.submit")}
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
            <path d="M5 12h14M13 6l6 6-6 6" />
          </svg>
        </Button>
      </form>
      <div className="av-sign-in__note" role="note">
        <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="5" y="10" width="14" height="10" rx="2" />
          <path d="M8 10V7a4 4 0 0 1 8 0v3" />
        </svg>
        <p>
          {t("sign_in.note.code")} {t("sign_in.note.lost")}{" "}
          <Button variant="quiet" onClick={() => go("recover")}>
            {t("sign_in.use_backup")}
          </Button>
          {t("sign_in.note.link_end")} {t("sign_in.note.private")}
        </p>
      </div>
      <SupportLine />
    </>
  );
}

function CodeForm({ onDone, submitLabel, secret }: { onDone: (r: Schemas["Verified"]) => void; submitLabel: string; secret?: string }) {
  const [code, setCode] = useState("");
  const { error, busy, submit } = useSubmit(() => unwrap(api.POST("/v1/auth/mfa/verify", { body: { code } })), onDone);
  return (
    <>
      <ErrorNotice error={error} />
      <form className="av-stack" onSubmit={submit} noValidate>
        <CodeField label={t("ui.code_label")} value={code} onChange={setCode} autoFocus />
        <Button type="submit" wide disabled={busy}>
          {submitLabel}
        </Button>
        <DevSkipCode onVerified={onDone} secret={secret} />
      </form>
    </>
  );
}

function CodeStep({ onDone, go }: { onDone: (r: Schemas["Verified"]) => void; go: (s: Step) => void }) {
  return (
    <>
      <h1>{t("sign_in.code.title")}</h1>
      <p>{t("sign_in.code.intro")}</p>
      <CodeForm onDone={onDone} submitLabel={t("sign_in.code.submit")} />
      <Button variant="quiet" onClick={() => go("recover")}>
        {t("sign_in.use_backup")}
      </Button>
    </>
  );
}

function EnrolStep({ onDone }: { onDone: (r: Schemas["Verified"]) => void }) {
  const [setup, setSetup] = useState<Schemas["Enrolment"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    unwrap(api.POST("/v1/auth/mfa/enroll")).then(
      (result) => live && setSetup(result),
      (err: unknown) => live && setError(errorMessage(err)),
    );
    return () => {
      live = false;
    };
  }, []);
  return (
    <>
      <h1>{t("sign_in.enrol.title")}</h1>
      <p>{t("sign_in.enrol.intro")}</p>
      <ErrorNotice error={error} />
      {!setup && !error && <Loading />}
      {setup && (
        <>
          <ol className="av-stack">
            <li>{t("sign_in.enrol.step_app")}</li>
            <li>
              <p>{t("sign_in.enrol.step_scan")}</p>
              <img className="av-qr" src={setup.qr_code} alt={t("sign_in.enrol.qr_alt")} />
              <p>{t("sign_in.enrol.key")}</p>
              <p>
                <code className="av-mono">{setup.secret}</code>
              </p>
            </li>
            <li>{t("sign_in.enrol.step_code")}</li>
          </ol>
          <CodeForm onDone={onDone} submitLabel={t("sign_in.code.submit")} secret={setup.secret} />
        </>
      )}
    </>
  );
}

function BackupCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  return (
    <>
      <h1>{t("sign_in.codes.title")}</h1>
      <p>{t("sign_in.codes.intro")}</p>
      <ul className="av-codes">
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ul>
      <Button wide onClick={onDone}>
        {t("sign_in.codes.saved")}
      </Button>
    </>
  );
}

function RecoverStep({ onDone, go }: { onDone: (r: Schemas["SignInResult"]) => void; go: (s: Step) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const { error, busy, submit } = useSubmit(
    () => unwrap(api.POST("/v1/auth/mfa/recover", { body: { email, password, code } })),
    onDone,
    { ...CREDENTIAL_ERRORS, invalid_code: "errors.invalid_code" },
  );
  return (
    <>
      <h1>{t("sign_in.recover.title")}</h1>
      <p>{t("sign_in.recover.intro")}</p>
      <ErrorNotice error={error} />
      <form className="av-stack" onSubmit={submit} noValidate>
        <TextField label={t("sign_in.email")} name="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <TextField label={t("sign_in.password")} name="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        <TextField label={t("sign_in.recover.code")} name="backup_code" autoComplete="off" required className="av-mono" value={code} onChange={(e) => setCode(e.target.value.trim())} />
        <Button type="submit" wide disabled={busy}>
          {t("sign_in.submit")}
        </Button>
      </form>
      <Button variant="quiet" onClick={() => go("password")}>
        {t("sign_in.back")}
      </Button>
    </>
  );
}

function ForgotStep({ go }: { go: (s: Step) => void }) {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const { error, busy, submit } = useSubmit(() => unwrap(api.POST("/v1/auth/password/forgot", { body: { email } })), () => setSent(true));
  return (
    <>
      <h1>{t("sign_in.forgot_form.title")}</h1>
      {sent ? (
        <Notice tone="success">
          <p>{t("sign_in.forgot_form.sent")}</p>
        </Notice>
      ) : (
        <>
          <p>{t("sign_in.forgot_form.intro")}</p>
          <ErrorNotice error={error} />
          <form className="av-stack" onSubmit={submit} noValidate>
            <TextField label={t("sign_in.email")} name="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
            <Button type="submit" wide disabled={busy}>
              {t("sign_in.forgot_form.submit")}
            </Button>
          </form>
        </>
      )}
      <Button variant="quiet" onClick={() => go("password")}>
        {t("sign_in.back")}
      </Button>
    </>
  );
}
