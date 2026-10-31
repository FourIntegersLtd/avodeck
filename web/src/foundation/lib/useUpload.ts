// What a screen that uploads needs: a signal that fires when the screen goes away (so the upload is
// cancelled, not left running), and the current step in plain words (chunkedUpload.ts reports the phase).
import { useCallback, useEffect, useRef, useState } from "react";
import type { UploadOptions, UploadPhase } from "./chunkedUpload";
import { uploadPhaseMessage } from "./uploadPhases";

export function useUpload() {
  const control = useRef<AbortController | null>(null);
  const [phase, setPhase] = useState<UploadPhase | null>(null);
  useEffect(() => {
    // React may run an effect's cleanup and set it up again (strict mode): start afresh if that aborted it.
    if (control.current?.signal.aborted) control.current = null;
    return () => control.current?.abort();
  }, []);
  /** Options for `uploadInParts`: pass these on every upload this screen starts. */
  const options = useCallback((): UploadOptions => {
    control.current ??= new AbortController();
    return { signal: control.current.signal, onProgress: ({ phase: p }) => setPhase(p === "done" || p === "failed" ? null : p) };
  }, []);
  return { options, status: phase ? uploadPhaseMessage(phase) : null };
}
