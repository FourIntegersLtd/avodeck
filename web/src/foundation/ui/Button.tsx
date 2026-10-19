import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { LinkProps } from "react-router-dom";
import { SafeLink as Link } from "./SafeLink";

type Variant = "primary" | "secondary" | "quiet" | "danger";

function classes(variant: Variant, wide?: boolean, extra?: string) {
  return ["av-button", variant !== "primary" && `av-button--${variant}`, wide && "av-button--wide", extra].filter(Boolean).join(" ");
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  wide?: boolean;
  children: ReactNode;
}

/** A button at least 52px tall. It never submits a form unless asked to. */
export function Button({ variant = "primary", wide, type = "button", className, ...rest }: ButtonProps) {
  return <button type={type} className={classes(variant, wide, className)} {...rest} />;
}

export interface LinkButtonProps extends LinkProps {
  variant?: Variant;
  wide?: boolean;
  /** Adds the arrow of a call to action that moves on ("Prepare my bundle →"). */
  arrow?: boolean;
}

/** A link that looks like a button (it opens a screen rather than doing something). */
export function LinkButton({ variant = "primary", wide, arrow, className, children, ...rest }: LinkButtonProps) {
  return (
    <Link className={classes(variant, wide, arrow ? ["av-button--arrow", className].filter(Boolean).join(" ") : className)} {...rest}>
      {children}
      {arrow && (
        <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M5 12h14M13 6l6 6-6 6" />
        </svg>
      )}
    </Link>
  );
}
