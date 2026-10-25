// Help (the header's "Help"): a form that emails support (ADR 0022).
import { useState } from "react";
import { useLocation } from "react-router-dom";
import { t } from "../../foundation/i18n";
import { useBusy } from "../../foundation/lib/useBusy";
import { api, ApiError, unwrap } from "../../foundation/platform-client";
import { Button, ChoicePills, Notice } from "../../foundation/ui";

const TOPICS = ["case", "account", "billing", "other"] as const;
type Topic = (typeof TOPICS)[number];

function HelpForm() {
  const { pathname } = useLocation();
  const [topic, setTopic] = useState<Topic>("case");
  const [message, setMessage] = useState("");
  const [state, setState] = useState<"idle" | "sent" | "too_many" | "failed">("idle");
  const { busy, run } = useBusy();

  const send = () =>
    run(async () => {
      try {
        await unwrap(api.POST("/v1/help-requests", { body: { topic, message, page: pathname.replace(/[^A-Za-z0-9/_-]/g, "").slice(0, 201) || "/" } }));
        setMessage("");
        setState("sent");
      } catch (e) {
        setState(e instanceof ApiError && e.status === 429 ? "too_many" : "failed");
      }
    });

  return (
    <form className="av-stack" onSubmit={(e) => { e.preventDefault(); void send(); }}>
      <ChoicePills
        label={t("help.form.topic")}
        value={topic}
        onChange={(v) => setTopic(v as Topic)}
        options={TOPICS.map((value) => ({ value, label: t(`help.form.topic.${value}`) }))}
      />
      <div className="av-field">
        <label className="av-field__label" htmlFor="help-message">{t("help.form.message")}</label>
        <p className="av-field__hint">{t("help.form.warning")}</p>
        <textarea id="help-message" className="av-textarea" rows={6} maxLength={4000} value={message} onChange={(e) => setMessage(e.target.value)} />
      </div>
      {state === "sent" && <Notice tone="success">{t("help.form.sent")}</Notice>}
      {state === "too_many" && <Notice tone="error">{t("help.form.too_many")}</Notice>}
      {state === "failed" && <Notice tone="error">{t("help.form.failed")}</Notice>}
      <Button type="submit" busy={busy} disabled={!message.trim()}>{t("help.form.send")}</Button>
    </form>
  );
}

export function Help() {
  return (
    <div className="av-page av-page--narrow av-stack">
      <h1>{t("help.title")}</h1>
      <p>{t("help.intro")}</p>
      <HelpForm />
    </div>
  );
}
