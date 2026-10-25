// Rule 10: risky actions need the code entered in the last 10 minutes. When the API answers
// step_up_required or mfa_required, this asks for the code (POST /v1/auth/mfa/verify) and then tries
// again. Sign-in is password only (ADR 0025), so the first risky action may find no authenticator yet
// (mfa_required with mfa_enrolled false): then it sets one up (picture, key, code, the backup codes once)
// before trying again. If the person cancels, or the code is still asked for after it was given, it
// says that nothing was changed.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { t } from "../i18n";
import { api, ApiError, CODE_NEEDED, onStepUpRequired, unwrap, type Schemas } from "../platform-client";
import { Button, CodeField, Dialog, Loading, Notice } from "../ui";
import { BackupCodeList, SetupSteps } from "./authenticator";
import { DevSkipCode } from "./DevSkipCode";
import { errorMessage } from "./errors";

type Run = <T>(action: () => Promise<T>) => Promise<T | undefined>;

const StepUpContext = createContext<Run | null>(null);

export function StepUpProvider({ children }: { children: ReactNode }) {
  const [asking, setAsking] = useState<"code" | "setup" | null>(null);
  const [cancelled, setCancelled] = useState(false);
  const answer = useRef<((ok: boolean) => void) | null>(null);

  const ask = (setup: boolean) =>
    new Promise<boolean>((resolve) => {
      answer.current = resolve;
      setAsking(setup ? "setup" : "code");
    });

  const done = (ok: boolean) => {
    setAsking(null);
    answer.current?.(ok);
    answer.current = null;
  };

  // Every API call that needs the code asks here first and is sent again once (platform-client).
  useEffect(
    () =>
      onStepUpRequired({
        ask: async (setup) => {
          setCancelled(false);
          const ok = await ask(setup);
          if (!ok) setCancelled(true);
          return ok;
        },
        nothingChanged: () => setCancelled(true),
      }),
    [],
  );

  const run = useCallback<Run>(async (action) => {
    setCancelled(false);
    try {
      return await action();
    } catch (error) {
      if (!needsCode(error)) throw error;
      if (error instanceof ApiError && error.stepUpAsked) return nothingChanged(); // already asked once
      if (!(await ask(needsSetup(error)))) return nothingChanged();
      try {
        return await action();
      } catch (again) {
        if (!needsCode(again)) throw again;
        return nothingChanged();
      }
    }
  }, []);

  const nothingChanged = () => {
    setCancelled(true);
    return undefined;
  };

  return (
    <StepUpContext.Provider value={run}>
      {children}
      {cancelled && (
        <div className="av-stepup-notice">
          <Notice tone="warning">
            <p>{t("step_up.cancelled")}</p>
            <Button variant="secondary" onClick={() => setCancelled(false)}>
              {t("step_up.cancelled_ok")}
            </Button>
          </Notice>
        </div>
      )}
      {asking === "code" && <StepUpDialog onDone={done} />}
      {asking === "setup" && <SetupDialog onDone={done} />}
    </StepUpContext.Provider>
  );
}

const needsCode = (error: unknown): error is ApiError => error instanceof ApiError && error.status === 403 && CODE_NEEDED.has(error.code);
const needsSetup = (error: ApiError) => error.extra.mfa_enrolled === false;

/** First risky action with no authenticator yet: set it up, show the backup codes once, then carry on. */
function SetupDialog({ onDone }: { onDone: (ok: boolean) => void }) {
  const [setup, setSetup] = useState<Schemas["Enrolment"] | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
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
  const verified = (result: Schemas["Verified"]) => {
    if (result.recovery_codes?.length) setCodes(result.recovery_codes);
    else onDone(true);
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      verified(await unwrap(api.POST("/v1/auth/mfa/verify", { body: { code } })));
    } catch (err) {
      setError(errorMessage(err));
    }
    setBusy(false);
  };
  if (codes) {
    return (
      <Dialog title={t("sign_in.codes.title")} onClose={() => onDone(true)}>
        <div className="av-stack">
          <BackupCodeList codes={codes} />
          <div className="av-row">
            <Button onClick={() => onDone(true)}>{t("sign_in.codes.saved")}</Button>
          </div>
        </div>
      </Dialog>
    );
  }
  return (
    <Dialog title={t("step_up.setup.title")} onClose={() => onDone(false)}>
      <form className="av-stack" onSubmit={(e) => void submit(e)} noValidate>
        <p>{t("step_up.setup.body")}</p>
        {error && (
          <Notice tone="error">
            <p>{error}</p>
          </Notice>
        )}
        {!setup && !error && <Loading inline />}
        {setup && (
          <>
            <SetupSteps setup={setup} />
            <CodeField label={t("ui.code_label")} value={code} onChange={setCode} />
          </>
        )}
        <div className="av-row">
          {setup && (
            <Button type="submit" disabled={busy}>
              {t("step_up.submit")}
            </Button>
          )}
          <Button variant="secondary" onClick={() => onDone(false)}>
            {t("step_up.cancel")}
          </Button>
          {setup && <DevSkipCode onVerified={verified} secret={setup.secret} />}
        </div>
      </form>
    </Dialog>
  );
}

function StepUpDialog({ onDone }: { onDone: (ok: boolean) => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await unwrap(api.POST("/v1/auth/mfa/verify", { body: { code } }));
      onDone(true);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  };
  return (
    <Dialog title={t("step_up.title")} onClose={() => onDone(false)}>
      <form className="av-stack" onSubmit={(e) => void submit(e)} noValidate>
        <p>{t("step_up.body")}</p>
        {error && (
          <Notice tone="error">
            <p>{error}</p>
          </Notice>
        )}
        <CodeField label={t("ui.code_label")} value={code} onChange={setCode} />
        <div className="av-row">
          <Button type="submit" disabled={busy}>
            {t("step_up.submit")}
          </Button>
          <Button variant="secondary" onClick={() => onDone(false)}>
            {t("step_up.cancel")}
          </Button>
          <DevSkipCode onVerified={() => onDone(true)} />
        </div>
      </form>
    </Dialog>
  );
}

/** Runs an action, asking for the code first if the API needs it. Resolves undefined if cancelled. */
export function useStepUp(): Run {
  const run = useContext(StepUpContext);
  if (!run) throw new Error("useStepUp outside StepUpProvider");
  return run;
}
