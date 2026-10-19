/** A turning arc for work in progress. Always paired with words, so it is hidden from screen readers. */
export function Spinner() {
  return (
    <svg className="av-spinner" width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" strokeWidth="3" strokeLinecap="round">
      <circle className="av-spinner__track" cx="12" cy="12" r="9" />
      <path className="av-spinner__arc" d="M12 3a9 9 0 0 1 9 9" />
    </svg>
  );
}
