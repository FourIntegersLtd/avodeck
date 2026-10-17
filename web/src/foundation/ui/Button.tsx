import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link, type LinkProps } from "react-router-dom";

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
}

/** A link that looks like a button (it opens a screen rather than doing something). */
export function LinkButton({ variant = "primary", wide, className, ...rest }: LinkButtonProps) {
  return <Link className={classes(variant, wide, className)} {...rest} />;
}
