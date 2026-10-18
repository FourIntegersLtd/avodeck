// Quiet time-keeping on every case screen (mounted by the case frame for any screen a pack step holds): every
// 30 seconds say which screen is open, whether the tab is
// visible and how long since the last key or touch. The server decides what counts (visible, recent input) and
// which workflow step the screen belongs to; a refused beat is ignored.
import { useEffect } from "react";
import { api } from "../platform-client";

const BEAT_MS = 30_000;
const INPUTS = ["keydown", "pointerdown", "pointermove", "wheel", "touchstart"] as const;

/** Beats only while `screen` is set (a pack screen key); null keeps quiet. */
export function useTimeHeartbeat(caseId: string, screen: string | null): void {
  useEffect(() => {
    if (!screen) return undefined;
    let lastInput = Date.now();
    const touched = () => {
      lastInput = Date.now();
    };
    INPUTS.forEach((name) => document.addEventListener(name, touched, { passive: true }));
    const timer = window.setInterval(() => {
      const idle = Math.max(0, Math.round((Date.now() - lastInput) / 1000));
      void api
        .POST("/v1/cases/{case_id}/time/heartbeat", {
          params: { path: { case_id: caseId } },
          body: { screen, visible: document.visibilityState === "visible", idle_seconds: idle },
        })
        .catch(() => undefined);
    }, BEAT_MS);
    return () => {
      window.clearInterval(timer);
      INPUTS.forEach((name) => document.removeEventListener(name, touched));
    };
  }, [caseId, screen]);
}
