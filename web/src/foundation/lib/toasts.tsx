// Short "this is done" messages that stay on screen while the person moves around the app.
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
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

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);
  const close = useCallback((id: number) => setToasts((prev) => prev.filter((x) => x.id !== id)), []);
  const show = useCallback(
    (toast: ToastInput) => {
      const id = next.current++;
      setToasts((prev) => [...prev, { ...toast, id }]);
      window.setTimeout(() => close(id), SHOW_MS);
    },
    [close],
  );
  const value = useMemo(() => ({ show }), [show]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="av-toasts" role="region" aria-label={t("app.toast.region")}>
        {toasts.map((x) => (
          <div key={x.id} className={`av-toast av-toast--${x.tone ?? "success"}`} role={x.tone === "error" ? "alert" : "status"}>
            <p>{x.text}</p>
            {x.action && <Link to={x.action.to} onClick={() => close(x.id)}>{x.action.label}</Link>}
            <button type="button" className="av-toast__close" onClick={() => close(x.id)}>{t("app.toast.close")}</button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/** Show a toast. Outside the provider it does nothing, so screens render in isolation. */
export function useToast(): (toast: ToastInput) => void {
  return useContext(ToastContext)?.show ?? (() => undefined);
}
