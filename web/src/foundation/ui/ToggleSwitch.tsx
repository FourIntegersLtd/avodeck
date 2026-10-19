export interface ToggleSwitchProps {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}

/** An on/off switch: a button with `role="switch"`, so it says whether it is on, not only by colour. */
export function ToggleSwitch({ label, checked, onChange, disabled }: ToggleSwitchProps) {
  return (
    <button type="button" role="switch" aria-checked={checked} className="av-switch" disabled={disabled} onClick={() => onChange(!checked)}>
      <svg className="av-switch__track" width="52" height="30" viewBox="0 0 52 30" aria-hidden="true" focusable="false">
        <rect className="av-switch__bg" x="0" y="0" width="52" height="30" rx="15" />
        <circle className="av-switch__knob" cx={checked ? 37 : 15} cy="15" r="11" />
      </svg>
      <span>{label}</span>
    </button>
  );
}
