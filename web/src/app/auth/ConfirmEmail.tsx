import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { api, unwrap } from "../../foundation/platform-client";
import { Loading, Notice } from "../../foundation/ui";
import { AuthLayout } from "./AuthLayout";

/** Opened from an email-change email. The change happens once both addresses have confirmed. */
export function ConfirmEmail() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [result, setResult] = useState<{ status?: string; error?: string } | null>(null);
  const sent = useRef(false);

  useEffect(() => {
    if (sent.current) return; // a link works once, so never send it twice
    sent.current = true;
    unwrap(api.POST("/v1/me/email-change/confirm", { body: { token } })).then(
      (r) => setResult({ status: r.status }),
      (err: unknown) => setResult({ error: errorMessage(err) }),
    );
  }, [token]);

  return (
    <AuthLayout>
      <h1>{t("email_confirm.title")}</h1>
      {!result && <Loading inline />}
      {result?.error && (
        <Notice tone="error">
          <p>{result.error}</p>
        </Notice>
      )}
      {result?.status && (
        <Notice tone="success">
          <p>{t(result.status === "email_changed" ? "email_confirm.changed" : "email_confirm.waiting")}</p>
        </Notice>
      )}
      <Link className="av-button av-button--secondary" to="/today">
        {t("email_confirm.continue")}
      </Link>
    </AuthLayout>
  );
}
