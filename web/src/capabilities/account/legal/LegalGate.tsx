import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiOrigin } from "../../../foundation/platform-client";
import { Button, Checkbox } from "../../../foundation/ui";
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
  { key: "terms", title: "Terms of service", summary: "The agreement between you and Four Integers Ltd when you use Avodeck", content: TermsContent },
  { key: "privacy", title: "Privacy policy", summary: "What personal data we hold, why, and the choices you have", content: PrivacyContent },
  { key: "dpa", title: "Data processing agreement", summary: "How we handle the medical records you upload, under Article 28 UK GDPR", content: DpaContent },
] as const;

function Chevron() {
  return (
    <svg className="av-legal-panel__chevron" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

export function LegalGate({ from, onDone }: { from?: string; onDone?: () => void }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [agreed, setAgreed] = useState(false);

  function toggle(key: string) {
    setOpen((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  async function accept() {
    const res = await fetch(`${apiOrigin()}/v1/legal-agreements/accept`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
    });
    if (!res.ok) throw new Error(`Failed: ${res.status}`);
    if (onDone) onDone();
    else navigate(from || "/today", { replace: true });
  }

  return (
    <div className="av-legal-gate">
      <h1>Before you start</h1>
      <p>
        Please read and agree to our terms, privacy policy and data processing
        agreement. You can open each one below.
      </p>

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
                <strong>{title}</strong>
                <span>{summary}</span>
              </div>
              <Chevron />
            </button>
            {open[key] && (
              <div id={`legal-${key}`} className="av-legal-panel__body" role="region" aria-label={title}>
                <Content />
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="av-legal-agree">
        <Checkbox
          label="I have read and agree to the Terms of service, Privacy policy and Data processing agreement"
          checked={agreed}
          onChange={(e) => setAgreed(e.target.checked)}
        />
      </div>

      <div className="av-legal-actions">
        <Button disabled={!agreed} onClick={accept} arrow>
          Continue
        </Button>
      </div>
    </div>
  );
}
