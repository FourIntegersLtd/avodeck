// The plain words for each step of an upload (chunkedUpload.ts reports the phase; screens show these).
import { t } from "../i18n";
import type { UploadPhase } from "./chunkedUpload";

/** Words for an upload phase. Processing is not a phase: screens show the pipeline's own progress after "Saved". */
export const uploadPhaseMessage = (phase: UploadPhase): string => t(`upload.phase.${phase}`);
