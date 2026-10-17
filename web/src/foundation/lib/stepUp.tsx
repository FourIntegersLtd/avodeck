// Rule 10: risky actions need the code entered in the last 10 minutes. When the API answers
// step_up_required, this asks for the code (POST /v1/auth/mfa/verify) and then tries again.
import { createContext, useCallback, useContext, useRef, useState, type FormEvent, type ReactNode } from "react";
import { t } from "../i18n";
import { api, ApiError, unwrap } from "../platform-client";
import { Button, CodeField, Dialog, Notice } from "../ui";
import { DevSkipCode } from "./DevSkipCode";
import { errorMessage } from "./errors";

type Run = <T>(action: () => Promise<T>) => Promise<T | undefined>;

const StepUpContext = createContext<Run | null>(null);

export function StepUpProvider({ children }: { children: ReactNode }) {
  const [asking, setAsking] = useState(false);
  const answer = useRef<((ok: boolean) => void) | null>(null);

  const ask = () =>
    new Promise<boolean>((resolve) => {
      answer.current = resolve;
      setAsking(true);
    });

  const done = (ok: boolean) => {
    setAsking(false);
    answer.current?.(ok);
    answer.current = null;
  };

  const run = useCallback<Run>(async (action) => {
    try {
      return await action();
    } catch (error) {
      if (!(error instanceof ApiError && error.code === "step_up_required")) throw error;
      if (!(await ask())) return undefined;
      return action();
    }
  }, []);

  return (
    <StepUpContext.Provider value={run}>
      {children}
      {asking && <StepUpDialog onDone={done} />}
    </StepUpContext.Provider>
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
