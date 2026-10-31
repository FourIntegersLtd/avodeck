import { useId, type ReactNode } from "react";

export interface ListRowProps {
  /** A `StatusMark` (or any small mark) shown on the left. */
  mark?: ReactNode;
  title: ReactNode;
  /** The record's own words, in grey Georgia; shown in quotation marks on one line. */
  quote?: string;
  expanded: boolean;
  onToggle: () => void;
  /** Shown under the row while it is expanded. */
  children?: ReactNode;
  /** Softens the row while it is not the chosen one (done rows). */
  dim?: boolean;
}

/** One row of a list: hairline above, a button that opens the row in place. Put it inside a `ul`. */
export function ListRow({ mark, title, quote, expanded, onToggle, children, dim }: ListRowProps) {
  const id = useId();
  return (
    <li className={`av-list-row${expanded ? " av-list-row--open" : ""}${dim && !expanded ? " av-list-row--dim" : ""}`}>
      <button type="button" className="av-list-row__button" aria-expanded={expanded} aria-controls={children && expanded ? id : undefined} onClick={onToggle}>
        {mark}
        <span className="av-list-row__text">
          <span className="av-list-row__title">{title}</span>
          {quote && <span className="av-list-row__quote">{`“${quote}”`}</span>}
        </span>
      </button>
      {expanded && children && (
        <div id={id} className="av-list-row__more">
          {children}
        </div>
      )}
    </li>
  );
}
