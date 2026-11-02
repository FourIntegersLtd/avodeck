// Short "this is done" messages that stay on screen while the person moves around the app.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { t } from "../i18n";

export interface ToastInput {
  text: string;
  tone?: "success" | "error";
  /** An optional link, e.g. "Open your papers". */
  action?: { label: string; to: string };
}
interface Toast extends ToastInput {
  id: number;
}

const SHOW_MS = 12000;
const ToastContext = createContext<{ show: (toast: ToastInput) => void } | null>(null);

/** One toast. Its timer stops while it is hovered or holds focus (WCAG 2.2.1) and is cleared on close and unmount. */
function ToastItem({ toast, onClose }: { toast: Toast; onClose: (id: number) => void }) {
  const [held, setHeld] = useState({ hover: false, focus: false });
  const paused = held.hover || held.focus;
  useEffect(() => {
    if (paused) return;
    const timer = window.setTimeout(() => onClose(toast.id), SHOW_MS);
    return () => window.clearTimeout(timer);
  }, [paused, toast.id, onClose]);
  return (
    <div
      className={`av-toast av-toast--${toast.tone ?? "success"}`}
      role={toast.tone === "error" ? "alert" : undefined}
      onMouseEnter={() => setHeld((h) => ({ ...h, hover: true }))}
      onMouseLeave={() => setHeld((h) => ({ ...h, hover: false }))}
      onFocus={() => setHeld((h) => ({ ...h, focus: true }))}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setHeld((h) => ({ ...h, focus: false }));
      }}
    >
      <p>{toast.text}</p>
      {toast.action && <Link to={toast.action.to} onClick={() => onClose(toast.id)}>{toast.action.label}</Link>}
      <button type="button" className="av-toast__close" onClick={() => onClose(toast.id)}>{t("app.toast.close")}</button>
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);
  const close = useCallback((id: number) => setToasts((prev) => prev.filter((x) => x.id !== id)), []);
  const show = useCallback((toast: ToastInput) => {
    const id = next.current++;
    setToasts((prev) => [...prev, { ...toast, id }]);
  }, []);
  const value = useMemo(() => ({ show }), [show]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* The live region is always in the page, so a message added to it is announced. */}
      <div className="av-toasts" role="status" aria-live="polite" aria-label={t("app.toast.region")}>
        {toasts.map((x) => (
          <ToastItem key={x.id} toast={x} onClose={close} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/** Show a toast. Outside the provider it does nothing, so screens render in isolation. */
export function useToast(): (toast: ToastInput) => void {
  return useContext(ToastContext)?.show ?? (() => undefined);
}
