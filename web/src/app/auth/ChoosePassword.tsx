import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { api, unwrap } from "../../foundation/platform-client";
import { AuthLayout, Button, Notice, TextField } from "../../foundation/ui";

const OWN = {
  wrong_password: "choose_password.wrong",
  same_password: "choose_password.same",
  invalid: "reset.too_short",
  weak_password: "choose_password.weak",
};

/** A person signed in with a temporary password from Avodeck support chooses their own before anything else
 * (ADR 0024). The server holds the block (403 `password_change_required`); this screen only asks. */
export function ChoosePassword({ onDone }: { onDone: () => void }) {
  const navigate = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await unwrap(api.POST("/v1/auth/password/change", { body: { current_password: current, new_password: next } }));
      setBusy(false);
      onDone();
    } catch (err) {
      setError(errorMessage(err, OWN));
      setBusy(false);
    }
  };
  const signOut = async () => {
    try {
      await unwrap(api.POST("/v1/auth/sign-out"));
    } finally {
      navigate("/sign-in", { replace: true });
    }
  };

  return (
    <AuthLayout>
      <h1>{t("choose_password.title")}</h1>
      <p>{t("choose_password.intro")}</p>
      {error && (
        <Notice tone="error">
          <p>{error}</p>
        </Notice>
      )}
      <form className="av-stack" onSubmit={(e) => void submit(e)} noValidate>
        <TextField
          label={t("choose_password.current")}
          name="current_password"
          type="password"
          autoComplete="current-password"
          required
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
        <TextField
          label={t("choose_password.new")}
          hint={t("reset.hint")}
          name="new_password"
          type="password"
          autoComplete="new-password"
          required
          value={next}
          onChange={(e) => setNext(e.target.value)}
        />
        <Button type="submit" wide busy={busy}>
          {t("choose_password.submit")}
        </Button>
      </form>
      <Button variant="quiet" onClick={() => signOut()}>
        {t("choose_password.sign_out")}
      </Button>
    </AuthLayout>
  );
}
