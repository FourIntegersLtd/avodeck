import { useId } from "react";

/** A calm turning arc for work in progress: a green head with a tail that fades into the track.
 * Always paired with words, so it is hidden from screen readers. Under reduced motion it pulses. */
export function Spinner() {
  const tail = `av-spinner-tail-${useId().replace(/:/g, "")}`;
  return (
    <svg className="av-spinner" width="44" height="44" viewBox="0 0 48 48" aria-hidden="true" focusable="false" fill="none" strokeWidth="5" strokeLinecap="round">
      <defs>
        <linearGradient id={tail} x1="1" y1="0" x2="0" y2="1">
          <stop offset="0" className="av-spinner__head" stopOpacity="0" />
          <stop offset="1" className="av-spinner__head" />
        </linearGradient>
      </defs>
      <circle className="av-spinner__track" cx="24" cy="24" r="20" />
      <path className="av-spinner__arc" d="M24 4a20 20 0 1 1 -20 20" stroke={`url(#${tail})`} />
    </svg>
  );
}
