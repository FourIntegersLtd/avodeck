// Shown when a person comes back to a screen that was sending a file when the page was closed or refreshed.
// The server is asked first; an open upload asks for the same file again, and only what is missing is sent.
import { useEffect, useId, useState, type ChangeEvent } from "react";
import { t } from "../i18n";
import { Button, Notice } from "../ui";
import { errorMessage } from "./errors";
import { discardResumable, findResumable, resumeUpload, type Resumable } from "./uploadResume";
import { UploadStatus } from "./UploadControls";
import { useUpload } from "./useUpload";

export function ResumeUpload({ caseId, onDocument }: { caseId: string; onDocument: (documentId: string) => void | Promise<void> }) {
  const upload = useUpload();
  const { personId } = upload;
  const [found, setFound] = useState<Resumable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const input = useId();

  const finish = async (documentId: string, note: string) => {
    setFound(null);
    setNotice(note);
    await onDocument(documentId);
  };

  useEffect(() => {
    if (!personId) return;
    let live = true;
    void (async () => {
      const next = await findResumable(personId, caseId).catch(() => null);
      if (!live || !next) return;
      if (next.state === "complete") return void (await finish(next.documentId, t("upload.resume.recovered")));
      setFound(next);
      if (next.state === "finalizing") {
        upload.begin();
        try {
          await finish(await resumeUpload(caseId, next, null, upload.options()), t("upload.resume.recovered"));
        } catch (err) {
          if (live) setError(errorMessage(err));
          if (live) setFound(null);
        }
      }
    })();
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [personId, caseId]);

  const choose = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !found) return;
    setError(null);
    upload.begin();
    try {
      await finish(await resumeUpload(caseId, found, file, upload.options()), t("upload.resume.recovered"));
    } catch (err) {
      if (upload.stopped()) return void setFound(null);
      setError(errorMessage(err));
      // A different file keeps the record so the right one can still be chosen; any other failure ends it.
      if ((err as { code?: string }).code !== "upload_wrong_file") setFound(null);
    }
  };

  const startAgain = async () => {
    if (found && personId) await discardResumable(personId, caseId, found.record);
    setFound(null);
    setError(null);
  };

  return (
    <>
      {notice && <Notice><p role="status">{notice}</p></Notice>}
      {error && <Notice tone="error"><p role="alert">{error}</p></Notice>}
      {found?.state === "needs_file" && !upload.running && (
        <Notice>
          <h2>{t("upload.resume.title")}</h2>
          <p>{t("upload.resume.body")}</p>
          <label htmlFor={input} className="av-button av-button--primary">{t("upload.resume.choose")}</label>
          <input id={input} type="file" className="av-visually-hidden" onChange={(e) => void choose(e)} />
          <Button variant="secondary" onClick={startAgain}>{t("upload.resume.start_again")}</Button>
        </Notice>
      )}
      <UploadStatus upload={upload} />
    </>
  );
}
