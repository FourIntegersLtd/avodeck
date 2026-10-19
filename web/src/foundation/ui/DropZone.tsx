import { useState, type DragEvent, type ReactNode } from "react";
import { t } from "../i18n";
import { IconTile } from "./Callout";

export interface DropZoneProps {
  title: string;
  hint?: ReactNode;
  icon?: ReactNode;
  accept?: string;
  multiple?: boolean;
  disabled?: boolean;
  /** Anything shown under the words, such as "6 files · 1,268 pages". */
  children?: ReactNode;
  onFiles: (files: File[]) => void;
}

/**
 * A card that takes files by drag and drop, or by the "Choose files" control, which is a real file
 * input the keyboard can reach. The files go to the caller; nothing is read here.
 */
export function DropZone({ title, hint, icon, accept, multiple, disabled, children, onFiles }: DropZoneProps) {
  const [over, setOver] = useState(false);
  const stop = (e: DragEvent) => e.preventDefault();
  return (
    <div
      className={`av-dropzone${over ? " av-dropzone--over" : ""}`}
      onDragOver={(e) => {
        stop(e);
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        stop(e);
        setOver(false);
        const files = Array.from(e.dataTransfer?.files ?? []);
        if (!disabled && files.length) onFiles(files);
      }}
    >
      {icon && <IconTile>{icon}</IconTile>}
      <span className="av-dropzone__title">{title}</span>
      {hint && <span className="av-dropzone__hint">{hint}</span>}
      {children}
      <label className="av-dropzone__browse">
        <input
          type="file"
          className="av-dropzone__input"
          accept={accept}
          multiple={multiple}
          disabled={disabled}
          onChange={(e) => {
            const files = Array.from(e.currentTarget.files ?? []);
            e.currentTarget.value = "";
            if (files.length) onFiles(files);
          }}
        />
        <span>{t("ui.dropzone.choose")}</span>
      </label>
    </div>
  );
}
