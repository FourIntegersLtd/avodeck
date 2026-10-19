import type { ReactNode } from "react";

/** A rounded square that holds an icon: mint for good news, amber for "please look". */
export function IconTile({ tone = "mint", children }: { tone?: "mint" | "amber"; children: ReactNode }) {
  return (
    <span className={`av-icon-tile av-icon-tile--${tone}`} aria-hidden="true">
      {children}
    </span>
  );
}

export interface CalloutProps {
  title: ReactNode;
  children?: ReactNode;
  /** Buttons or links that answer the callout. */
  actions?: ReactNode;
}

/** An amber alert: what needs a look, with the ways to deal with it beside it. */
export function Callout({ title, children, actions }: CalloutProps) {
  return (
    <div className="av-callout" role="alert">
      <p className="av-callout__title">{title}</p>
      {children && <div className="av-callout__body">{children}</div>}
      {actions && <div className="av-callout__actions">{actions}</div>}
    </div>
  );
}
