// S04 "Who is this about?": the person's own name, other names and date of birth. They go to the server to be
// sealed so the reading tools can hide them (rule 7); they are never shown back, only whether they are saved.
import { useState, type FormEvent } from "react";
import { t } from "../../foundation/i18n";
import { errorMessage } from "../../foundation/lib/errors";
import { useResource } from "../../foundation/lib/useResource";
import { api, unwrap } from "../../foundation/platform-client";
import { Button, Notice, TextField, useBusy } from "../../foundation/ui";
import { Section } from "./shared";

export function SubjectIdentity({ caseId }: { caseId: string }) {
  const subject = useResource(
    () => unwrap(api.GET("/v1/cases/{case_id}/subject", { params: { path: { case_id: caseId } } })),
    ["subject", caseId],
  );
  const [name, setName] = useState("");
  const [others, setOthers] = useState("");
  const [born, setBorn] = useState("");
  // What the last Save recorded; null until a Save, and cleared by the next edit.
  const [justSaved, setJustSaved] = useState<{ dob: boolean } | null>(null);
  const [saved, setSaved] = useState<{ dob: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const saving = useBusy();
  const recorded = saved !== null || subject.data?.identity_recorded === true;
  const dobRecorded = saved ? saved.dob : subject.data?.date_of_birth_recorded === true;
  const edit = (set: (v: string) => void) => (e: { target: { value: string } }) => {
    setJustSaved(null);
    set(e.target.value);
  };
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await unwrap(
        api.PUT("/v1/cases/{case_id}/subject/identity", {
          params: { path: { case_id: caseId } },
          body: {
            name: name.trim(),
            other_names: others
              .split(",")
              .map((o) => o.trim())
              .filter(Boolean),
            date_of_birth: born.trim() || null,
          },
        }),
      );
      const result = { dob: born.trim() !== "" };
      setSaved(result);
      setJustSaved(result);
      setName("");
      setOthers("");
      setBorn("");
    } catch (err) {
      setError(errorMessage(err, { invalid: "setup.subject_identity.invalid" }));
    }
  };
  return (
    <>
      {subject.data && (!recorded || !dobRecorded) && (
        <Notice tone="warning">
          <p>{t(recorded ? "setup.subject_identity.banner_dob" : "setup.subject_identity.banner")}</p>
        </Notice>
      )}
      <Section
        id="setup-subject-identity"
        title={t("setup.subject_identity.title")}
        intro={t("setup.subject_identity.intro")}
      >
        {recorded && (
          <p>{t(dobRecorded ? "setup.subject_identity.saved" : "setup.subject_identity.saved_name_only")}</p>
        )}
        <form className="av-stack" onSubmit={(e) => void saving.run(() => save(e))}>
          <TextField
            label={t("setup.subject_identity.name")}
            name="subject_name"
            autoComplete="off"
            required
            value={name}
            onChange={edit(setName)}
          />
          <TextField
            label={t("setup.subject_identity.other_names")}
            hint={t("setup.subject_identity.other_names_hint")}
            name="subject_other_names"
            autoComplete="off"
            value={others}
            onChange={edit(setOthers)}
          />
          <TextField
            label={t("setup.subject_identity.date_of_birth")}
            hint={t("setup.subject_identity.date_of_birth_hint")}
            name="subject_date_of_birth"
            autoComplete="off"
            value={born}
            onChange={edit(setBorn)}
          />
          <div>
            <Button type="submit" variant="secondary" busy={saving.busy}>
              {t("setup.subject_identity.save")}
            </Button>
          </div>
          {justSaved && (
            <Notice tone="mint">
              <p>{t(justSaved.dob ? "setup.subject_identity.saved_now" : "setup.subject_identity.saved_now_no_dob")}</p>
            </Notice>
          )}
        </form>
        {error && (
          <Notice tone="error">
            <p>{error}</p>
          </Notice>
        )}
      </Section>
    </>
  );
}
