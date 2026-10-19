// Small line icons for slice A's cards (24px, drawn with SVG attributes only; the CSP forbids inline styles).
import type { ReactNode } from "react";

function Icon({ children, size = 24, width = 2 }: { children: ReactNode; size?: number; width?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth={width} strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

export const PageIcon = () => <Icon><path d="M6 3h9l3 3v15H6z" /><path d="M9 12h6M9 16h4" /></Icon>;
export const CopyIcon = () => <Icon><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V5a1 1 0 00-1-1H5a1 1 0 00-1 1v10a1 1 0 001 1h3" /></Icon>;
export const OtherPersonIcon = () => <Icon><circle cx="9" cy="8" r="3.5" /><path d="M3 20c0-3.5 2.7-6 6-6" /><path d="M15 14l6 6M21 14l-6 6" /></Icon>;
export const MicIcon = () => <Icon><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0014 0M12 18v3" /></Icon>;
export const SearchIcon = () => <Icon><circle cx="11" cy="11" r="7" /><path d="M20 20l-4-4" /></Icon>;
export const AlertIcon = () => <Icon width={2.25}><circle cx="12" cy="12" r="9" /><path d="M12 7v6M12 16.5v.5" /></Icon>;
export const LockIcon = () => <Icon size={20}><rect x="5" y="10" width="14" height="10" rx="2" /><path d="M8 10V7a4 4 0 018 0v3" /></Icon>;
export const TickIcon = () => <Icon size={18} width={3}><path d="M5 12l5 5 9-10" /></Icon>;
export const ArrowIcon = () => <Icon size={20} width={2.5}><path d="M5 12h14M13 6l6 6-6 6" /></Icon>;

/** The picture above "Preparing your bundle": pages being sorted. */
export function PagesPicture({ label }: { label: string }) {
  return (
    <svg width="96" height="96" viewBox="0 0 96 96" role="img" aria-label={label} className="pl-picture">
      <rect x="22" y="26" width="46" height="58" rx="4" className="pl-picture__back" />
      <rect x="28" y="18" width="46" height="58" rx="4" className="pl-picture__mid" />
      <rect x="34" y="10" width="46" height="58" rx="4" className="pl-picture__front" />
      <path d="M43 26h28M43 34h28M43 42h18" className="pl-picture__lines" />
      <circle cx="72" cy="66" r="15" className="pl-picture__badge" />
      <path d="M65 66l5 5 9-10" className="pl-picture__tick" />
    </svg>
  );
}
