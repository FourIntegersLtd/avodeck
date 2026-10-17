// Local development only: a "Skip" button beside the code field. It fetches the demo account's
// current code from the dev server (web/devMfa.ts) and sends the normal verify call, so the API
// still checks a real code (rule 10). Production builds drop it (import.meta.env.DEV is false).
import { useState } from "react";
import { t } from "../i18n";
import { api, unwrap, type Schemas } from "../platform-client";
import { Button } from "../ui";

export function DevSkipCode({ onVerified }: { onVerified: (r: Schemas["Verified"]) => void }) {
  const [busy, setBusy] = useState(false);
  if (!import.meta.env.DEV) return null;
  const skip = async () => {
    setBusy(true);
    try {
      const res = await fetch("/__dev/mfa-code");
      if (res.ok) onVerified(await unwrap(api.POST("/v1/auth/mfa/verify", { body: { code: await res.text() } })));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Button variant="quiet" disabled={busy} onClick={() => void skip()}>
      {t("dev.skip_code")}
    </Button>
  );
}
