import type { ReactNode } from "react";

/** A rounded square that holds an icon: mint for good news, amber for "please look". */
export function IconTile({ tone = "mint", children }: { tone?: "mint" | "amber"; children: ReactNode }) {
  return (
    <span className={`av-icon-tile av-icon-tile--${tone}`} aria-hidden="true">
      {children}
    </span>
  );
}

/** The "please look" mark: a triangle with an exclamation mark. */
const WARNING = (
  <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
    <path d="M12 9v4M12 17h.01" />
  </svg>
);

export interface CalloutProps {
  title: ReactNode;
  children?: ReactNode;
  /** Buttons or links that answer the callout. */
  actions?: ReactNode;
  /** The icon in the amber tile; a warning mark unless given another. */
  icon?: ReactNode;
}

/** An amber alert (boards S08, S17, S29): amber outline on white, an icon tile, the words, and the ways to deal with it. */
export function Callout({ title, children, actions, icon }: CalloutProps) {
  return (
    <div className="av-callout" role="alert">
      <IconTile tone="amber">{icon ?? WARNING}</IconTile>
      <div className="av-callout__text">
        <p className="av-callout__title">{title}</p>
        {children && <div className="av-callout__body">{children}</div>}
      </div>
      {actions && <div className="av-callout__actions">{actions}</div>}
    </div>
  );
}
