import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { api, unwrap } from "../../foundation/platform-client";
import { Button, Notice, TextField } from "../../foundation/ui";
import { AuthLayout } from "./AuthLayout";

/** Opened from the password email: choose a new password (the API then signs out everywhere). */
export function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await unwrap(api.POST("/v1/auth/password/reset", { body: { token, new_password: password } }));
      setDone(true);
    } catch (err) {
      setError(errorMessage(err, { invalid: "reset.too_short" }));
    }
    setBusy(false);
  };

  return (
    <AuthLayout>
      <h1>{t("reset.title")}</h1>
      {!token && (
        <Notice tone="error">
          <p>{t("reset.no_token")}</p>
        </Notice>
      )}
      {done ? (
        <>
          <Notice tone="success">
            <p>{t("reset.done")}</p>
          </Notice>
          <Link className="av-button" to="/sign-in">
            {t("reset.sign_in")}
          </Link>
        </>
      ) : (
        token && (
          <>
            {error && (
              <Notice tone="error">
                <p>{error}</p>
              </Notice>
            )}
            <form className="av-stack" onSubmit={(e) => void submit(e)} noValidate>
              <TextField
                label={t("reset.password")}
                hint={t("reset.hint")}
                name="new_password"
                type="password"
                autoComplete="new-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <Button type="submit" wide disabled={busy}>
                {t("reset.submit")}
              </Button>
            </form>
          </>
        )
      )}
    </AuthLayout>
  );
}
