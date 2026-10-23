// Small parts shared by slice O's screens.
import { useState } from "react";
import { t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useStepUp } from "../../foundation/lib/stepUp";
import { LinkButton, Notice } from "../../foundation/ui";
import "./organisations.css";

export type Message = { tone: "success" | "error"; text: string } | null;

export const OWN_ERRORS = {
  conflict: "organisations.error.conflict",
  forbidden: "organisations.error.forbidden",
  personal_organisation: "organisations.error.personal_organisation",
  last_owner: "organisations.error.last_owner",
  cases_open: "organisations.close_org.cases_open",
};

/** Runs a risky call (asking for the code first when the API wants it) and keeps the plain-words result. */
export function useAct(own: Record<string, string> = {}) {
  const stepUp = useStepUp();
  const [message, setMessage] = useState<Message>(null);
  const act = async <T,>(action: () => Promise<T>, success?: (result: T) => string): Promise<T | undefined> => {
    setMessage(null);
    try {
      const result = await stepUp(action);
      if (result !== undefined && success) setMessage({ tone: "success", text: success(result) });
      return result;
    } catch (err) {
      setMessage({ tone: "error", text: errorMessage(err, { ...OWN_ERRORS, ...own }) });
      return undefined;
    }
  };
  return { act, message, setMessage };
}

export function MessageNotice({ message }: { message: Message }) {
  if (!message) return null;
  return (
    <Notice tone={message.tone}>
      <p>{message.text}</p>
    </Notice>
  );
}

export function NotFoundPage() {
  return (
    <main className="av-page av-stack" id="main">
      <h1>{t("app.not_found.title")}</h1>
      <p>{t("app.not_found.body")}</p>
      <LinkButton to="/today">{t("app.not_found.action")}</LinkButton>
    </main>
  );
}

export function shortDate(iso: string, locale: string | null | undefined): string {
  try {
    return new Intl.DateTimeFormat(locale ?? undefined, { day: "numeric", month: "short" }).format(new Date(iso));
  } catch {
    return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" }).format(new Date(iso));
  }
}
