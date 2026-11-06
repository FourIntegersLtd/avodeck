// The one confirmation box (replaces the browser's confirm()): `const confirm = useConfirm()`, then
// `if (!(await confirm({ message }))) return;`. Two buttons, Confirm and Cancel; Escape means Cancel.
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { t } from "../i18n";
import { Button, Dialog } from "../ui";

export interface ConfirmOptions {
  message: string;
  /** The box's heading. Defaults to "Are you sure?". */
  title?: string;
  /** The yes button's words. Defaults to "Confirm". */
  confirmLabel?: string;
  /** The no button's words. Defaults to "Cancel". */
  cancelLabel?: string;
  /** Makes the yes button red, for something that cannot be undone. */
  danger?: boolean;
}

type Ask = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<Ask | null>(null);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [asking, setAsking] = useState<ConfirmOptions | null>(null);
  const answer = useRef<((ok: boolean) => void) | null>(null);

  const ask = useCallback<Ask>(
    (options) =>
      new Promise<boolean>((resolve) => {
        answer.current?.(false); // a second question settles the first as "no"
        answer.current = resolve;
        setAsking(options);
      }),
    [],
  );

  const settle = (ok: boolean) => {
    answer.current?.(ok);
    answer.current = null;
    setAsking(null);
  };

  return (
    <ConfirmContext.Provider value={ask}>
      {children}
      {asking && (
        <Dialog title={asking.title ?? t("ui.confirm.title")} onClose={() => settle(false)}>
          <p>{asking.message}</p>
          <div className="av-dialog__actions">
            <Button variant="secondary" onClick={() => settle(false)}>{asking.cancelLabel ?? t("ui.cancel")}</Button>
            <Button variant={asking.danger ? "danger" : "primary"} onClick={() => settle(true)}>{asking.confirmLabel ?? t("ui.confirm.yes")}</Button>
          </div>
        </Dialog>
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): Ask {
  const ask = useContext(ConfirmContext);
  if (!ask) throw new Error("useConfirm needs the ConfirmProvider");
  return ask;
}
