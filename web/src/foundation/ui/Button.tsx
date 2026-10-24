import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { LinkProps } from "react-router-dom";
import { SafeLink as Link } from "./SafeLink";

type Variant = "primary" | "secondary" | "outline" | "quiet" | "link" | "danger";

function classes(variant: Variant, wide?: boolean, extra?: string, arrow?: boolean | "back") {
  return ["av-button", variant !== "primary" && `av-button--${variant}`, wide && "av-button--wide", arrow && "av-button--arrow", arrow === "back" && "av-button--arrow-back", extra].filter(Boolean).join(" ");
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  wide?: boolean;
  /** Adds the arrow of a call to action that moves on ("Confirm and continue →"); "back" puts a left arrow before the words. */
  arrow?: boolean | "back";
  children: ReactNode;
}

/** A button at least 52px tall. It never submits a form unless asked to. */
export function Button({ variant = "primary", wide, arrow, type = "button", className, children, ...rest }: ButtonProps) {
  return (
    <button type={type} className={classes(variant, wide, className, arrow)} {...rest}>
      {arrow === "back" && <Arrow back />}
      {children}
      {arrow === true && <Arrow />}
    </button>
  );
}

function Arrow({ back }: { back?: boolean }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d={back ? "M19 12H5M11 6l-6 6 6 6" : "M5 12h14M13 6l6 6-6 6"} />
    </svg>
  );
}

export interface LinkButtonProps extends LinkProps {
  variant?: Variant;
  wide?: boolean;
  /** Adds the arrow of a call to action that moves on ("Prepare my bundle →"); "back" puts a left arrow before the words. */
  arrow?: boolean | "back";
}

/** A link that looks like a button (it opens a screen rather than doing something). */
export function LinkButton({ variant = "primary", wide, arrow, className, children, ...rest }: LinkButtonProps) {
  return (
    <Link className={classes(variant, wide, className, arrow)} {...rest}>
      {arrow === "back" && <Arrow back />}
      {children}
      {arrow === true && <Arrow />}
    </Link>
  );
}
