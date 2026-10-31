// What a screen that uploads needs: a signal that fires when the screen goes away (so the upload is
// cancelled, not left running), a way to stop it on purpose, and the current step in plain words
// (chunkedUpload.ts reports the phase). It also names who is signed in so a refresh can resume the upload.
import { useCallback, useContext, useEffect, useRef, useState } from "react";
import type { UploadOptions, UploadPhase } from "./chunkedUpload";
import { SessionContext } from "./session";
import { uploadPhaseMessage } from "./uploadPhases";

export function useUpload() {
  const control = useRef<AbortController | null>(null);
  const wasStopped = useRef(false);
  const personId = useContext(SessionContext)?.me.person.id;
  const [phase, setPhase] = useState<UploadPhase | null>(null);
  useEffect(() => {
    // React may run an effect's cleanup and set it up again (strict mode): start afresh if that aborted it.
    if (control.current?.signal.aborted) control.current = null;
    return () => control.current?.abort();
  }, []);
  /** Options for `uploadInParts`: pass these on every upload this screen starts. */
  const options = useCallback((): UploadOptions => {
    control.current ??= new AbortController();
    return { signal: control.current.signal, personId, onProgress: ({ phase: p }) => setPhase(p === "done" || p === "failed" ? null : p) };
  }, [personId]);
  /** Stops what is running on purpose; the client cancels it on the server and forgets its record. */
  const stop = useCallback(() => {
    wasStopped.current = true;
    control.current?.abort();
    control.current = null;
  }, []);
  /** Call when a new round of uploads begins, so an earlier stop is not mistaken for this one. */
  const begin = useCallback(() => {
    wasStopped.current = false;
  }, []);
  const stopped = useCallback(() => wasStopped.current, []);
  return { options, stop, begin, stopped, running: phase !== null, personId, status: phase ? uploadPhaseMessage(phase) : null };
}
