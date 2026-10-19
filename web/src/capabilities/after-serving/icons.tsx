// The small line icons the after-serving boards draw inside their icon tiles (24px, 2px stroke, round ends).
import type { ReactNode } from "react";

function Icon({ children, size = 24 }: { children: ReactNode; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

export const HelpIcon = () => <Icon><circle cx="12" cy="12" r="9" /><path d="M9.5 9a2.5 2.5 0 015 .5c0 1.5-2.5 2-2.5 3.5M12 17v.5" /></Icon>;
export const NewDocIcon = () => <Icon><path d="M6 3h9l3 3v15H6z" /><path d="M12 10v6M9 13h6" /></Icon>;
export const PencilIcon = () => <Icon><path d="M12 20h9M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z" /></Icon>;
export const GavelIcon = () => <Icon><path d="M14 4l6 6M11 7l6 6M4 20l7-7M9 5l10 10" /></Icon>;
export const LockIcon = ({ size = 20 }: { size?: number }) => <Icon size={size}><rect x="5" y="10" width="14" height="10" rx="2" /><path d="M8 10V7a4 4 0 018 0v3" /></Icon>;
export const TickIcon = () => <Icon size={18}><path d="M5 12l5 5 9-10" /></Icon>;
export const ArrowIcon = () => <Icon size={18}><path d="M5 12h14M13 6l6 6-6 6" /></Icon>;
export const WarnIcon = () => <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round"><circle cx="12" cy="12" r="9" /><path d="M12 7v6M12 16.5v.5" /></svg>;
export const PeopleIcon = () => <Icon><circle cx="9" cy="8" r="3.5" /><path d="M3 20c0-3.5 2.7-6 6-6s6 2.5 6 6" /><path d="M16 4.5a3.5 3.5 0 010 7M18 14c2 .7 3 2.9 3 6" /></Icon>;
export const TickGreenIcon = () => <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="#2F9E6E" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12l5 5 9-10" /></svg>;
