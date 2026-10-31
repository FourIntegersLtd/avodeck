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
  /** Files are in: a green outline and an "Added" mark with a tick (S04, S07 boards). */
  added?: boolean;
  /** Other words for "Added". */
  addedLabel?: string;
  /** A small mark beside the title, such as a star for what is needed now. */
  mark?: ReactNode;
  /** Anything shown under the words, such as "6 files · 1,268 pages". */
  children?: ReactNode;
  onFiles: (files: File[]) => void;
}

/**
 * A card that takes files by drag and drop, or by the "Choose files" control, which is a real file
 * input the keyboard can reach. The files go to the caller; nothing is read here.
 */
export function DropZone({ title, hint, icon, accept, multiple, disabled, added, addedLabel, mark, children, onFiles }: DropZoneProps) {
  const [over, setOver] = useState(false);
  const stop = (e: DragEvent) => e.preventDefault();
  return (
    <div
      className={`av-dropzone${over ? " av-dropzone--over" : ""}${added ? " av-dropzone--added" : ""}`}
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
      {(icon || added) && (
        <div className="av-dropzone__top">
          {icon && <IconTile>{icon}</IconTile>}
          {added && (
            <span className="av-dropzone__added">
              <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 12l5 5 9-10" />
              </svg>
              {addedLabel ?? t("ui.dropzone.added")}
            </span>
          )}
        </div>
      )}
      <span className="av-dropzone__titlerow">
        <span className="av-dropzone__title">{title}</span>
        {mark}
      </span>
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
