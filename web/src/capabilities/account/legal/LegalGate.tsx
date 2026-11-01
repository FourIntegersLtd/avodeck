import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { t } from "../../../foundation/i18n";
import { errorMessage } from "../../../foundation/lib/errors";
import { api, unwrap } from "../../../foundation/platform-client";
import { Button, Checkbox, Logo, Notice } from "../../../foundation/ui";
import { DpaContent } from "./DpaContent";
import { PrivacyContent } from "./PrivacyContent";
import { TermsContent } from "./TermsContent";
import "./legal.css";

interface DocumentStatus {
  document: string;
  current_version: string;
  accepted_version: string | null;
}

export interface LegalStatusResponse {
  accepted: boolean;
  documents: DocumentStatus[];
}

const PANELS = [
  { key: "terms", title: "account.legal.panels.terms.title", summary: "account.legal.panels.terms.summary", content: TermsContent },
  { key: "privacy", title: "account.legal.panels.privacy.title", summary: "account.legal.panels.privacy.summary", content: PrivacyContent },
  { key: "dpa", title: "account.legal.panels.dpa.title", summary: "account.legal.panels.dpa.summary", content: DpaContent },
] as const;

function Chevron() {
  return (
    <svg className="av-legal-panel__chevron" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

/** "Before you start" (S28 gate): the signed-in person must agree to the terms, privacy policy and
 * data processing agreement before anything else (RequireSession shows this in place of the app
 * until `GET /v1/legal-agreements/status` says `accepted`). A light brand header (just the mark, no
 * nav: nothing here makes sense to click before the person has agreed) sits above the panels. */
export function LegalGate({ from, onDone }: { from?: string; onDone?: () => void }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggle(key: string) {
    setOpen((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  async function accept() {
    setError(null);
    try {
      await unwrap(api.POST("/v1/legal-agreements/accept"));
      if (onDone) onDone();
      else navigate(from || "/today", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="av-legal-page">
      <div className="av-legal-page__brand">
        <Logo />
      </div>
      <div className="av-legal-gate">
        <h1>{t("account.legal.title")}</h1>
        <p>{t("account.legal.intro")}</p>

        {error && (
          <Notice tone="error">
            <p>{error}</p>
          </Notice>
        )}

        <div className="av-legal-panels">
          {PANELS.map(({ key, title, summary, content: Content }) => (
            <div key={key} className="av-legal-panel" data-open={open[key] ? "true" : "false"}>
              <button
                type="button"
                className="av-legal-panel__header"
                aria-expanded={open[key] ? "true" : "false"}
                aria-controls={`legal-${key}`}
                onClick={() => toggle(key)}
              >
                <div className="av-legal-panel__title">
                  <strong>{t(title)}</strong>
                  <span>{t(summary)}</span>
                </div>
                <Chevron />
              </button>
              {open[key] && (
                <div id={`legal-${key}`} className="av-legal-panel__body" role="region" aria-label={t(title)}>
                  <Content />
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="av-legal-agree">
          <Checkbox label={t("account.legal.agree")} checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
        </div>

        <div className="av-legal-actions">
          <Button disabled={!agreed} onClick={accept} arrow>
            {t("account.legal.continue")}
          </Button>
        </div>
      </div>
    </div>
  );
}
