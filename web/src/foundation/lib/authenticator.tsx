// The authenticator set-up pieces shared by sign-in (S01) and the step-up prompt (ADR 0025).
import { t } from "../i18n";
import type { Schemas } from "../platform-client";

/** The three steps: open the app, scan the picture (or type the key), type the code. */
export function SetupSteps({ setup }: { setup: Schemas["Enrolment"] }) {
  return (
    <ol className="av-stack">
      <li>{t("sign_in.enrol.step_app")}</li>
      <li>
        <p>{t("sign_in.enrol.step_scan")}</p>
        <img className="av-qr" src={setup.qr_code} alt={t("sign_in.enrol.qr_alt")} />
        <p>{t("sign_in.enrol.key")}</p>
        <p>
          <code className="av-mono">{setup.secret}</code>
        </p>
      </li>
      <li>{t("sign_in.enrol.step_code")}</li>
    </ol>
  );
}

/** The backup codes, shown once when set-up finishes. */
export function BackupCodeList({ codes }: { codes: string[] }) {
  return (
    <>
      <p>{t("sign_in.codes.intro")}</p>
      <ul className="av-codes">
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ul>
    </>
  );
}
