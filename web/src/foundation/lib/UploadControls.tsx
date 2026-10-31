// What a screen shows while a file is being sent: the step in plain words and one visible way to stop it.
import { t } from "../i18n";
import { Button } from "../ui";
import type { useUpload } from "./useUpload";

export function UploadStatus({ upload, withStatus = true }: { upload: ReturnType<typeof useUpload>; withStatus?: boolean }) {
  if (!upload.running) return null;
  return (
    <div className="av-upload-status">
      {withStatus && <p role="status" className="av-muted">{upload.status}</p>}
      <Button variant="secondary" onClick={upload.stop}>{t("upload.stop")}</Button>
    </div>
  );
}
