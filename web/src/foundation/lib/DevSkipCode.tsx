// Local development only: the code screens pass themselves. At setup the new authenticator key goes
// to the dev server (web/devMfa.ts), which keeps it per account and answers the current code; this
// sends the normal verify call, so the API still checks a real code (rule 10). The "Skip" button
// stays as a fallback (and is the only path under tests). Production builds drop it all
// (import.meta.env.DEV is false).
import { useEffect, useRef, useState } from "react";
import { t } from "../i18n";
import { api, unwrap, type Schemas } from "../platform-client";
import { Button } from "../ui";

let devEmail = "";

/** The account being signed in to, so the dev server answers that account's code. */
export function rememberDevEmail(email: string) {
  if (import.meta.env.DEV) devEmail = email.trim().toLowerCase();
}

export function DevSkipCode({ onVerified, secret }: { onVerified: (r: Schemas["Verified"]) => void; secret?: string }) {
  const [busy, setBusy] = useState(false);
  const tried = useRef(false);
  const skip = async () => {
    setBusy(true);
    try {
      /* eslint-disable no-restricted-globals -- the Vite dev server itself (web/devMfa.ts), never in a build */
      if (secret) await fetch("/__dev/mfa-secret", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: devEmail, secret }) });
      const res = await fetch(`/__dev/mfa-code?email=${encodeURIComponent(devEmail)}`);
      /* eslint-enable no-restricted-globals */
      if (res.ok) onVerified(await unwrap(api.POST("/v1/auth/mfa/verify", { body: { code: await res.text() } })));
    } catch {
      // The code field is still there to type into.
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (!import.meta.env.DEV || import.meta.env.MODE === "test" || tried.current) return;
    tried.current = true;
    void skip();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per screen
  }, []);
  if (!import.meta.env.DEV) return null;
  return (
    <Button variant="quiet" disabled={busy} onClick={() => void skip()}>
      {t("dev.skip_code")}
    </Button>
  );
}
