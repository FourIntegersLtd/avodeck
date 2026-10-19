import type { ReactNode } from "react";
import type { LinkProps } from "react-router-dom";
import { SafeLink as Link } from "./SafeLink";
import { t } from "../i18n";

/** A link that reads as a link (underlined, bold) with a 52px tap area. */
export function TextLink({ className, ...rest }: LinkProps) {
  return <Link className={["av-text-link", className].filter(Boolean).join(" ")} {...rest} />;
}

/** "← Back" above a screen. Says "Back" unless given other words. */
export function BackLink({ children, ...rest }: Omit<LinkProps, "children"> & { children?: ReactNode }) {
  return (
    <Link className="av-back-link" {...rest}>
      <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M19 12H5M11 6l-6 6 6 6" />
      </svg>
      {children ?? t("ui.back")}
    </Link>
  );
}
