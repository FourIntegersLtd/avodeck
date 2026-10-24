import type { ReactNode } from "react";

export interface NoticeProps {
  tone?: "info" | "error" | "warning" | "success" | "amber" | "mint";
  children: ReactNode;
}

/** A message box. Errors are announced straight away; other notices politely. */
export function Notice({ tone = "info", children }: NoticeProps) {
  return (
    <div className={`av-notice av-notice--${tone}`} role={tone === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}
