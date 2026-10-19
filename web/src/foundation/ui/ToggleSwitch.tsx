export interface ToggleSwitchProps {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  /** Words on the left, switch at the right end of the row (the settings rows of the boards). */
  labelFirst?: boolean;
}

/** An on/off switch: a button with `role="switch"`, so it says whether it is on, not only by colour. */
export function ToggleSwitch({ label, checked, onChange, disabled, labelFirst }: ToggleSwitchProps) {
  return (
    <button type="button" role="switch" aria-checked={checked} className={labelFirst ? "av-switch av-switch--label-first" : "av-switch"} disabled={disabled} onClick={() => onChange(!checked)}>
      {labelFirst && <span>{label}</span>}
      <svg className="av-switch__track" width="52" height="30" viewBox="0 0 52 30" aria-hidden="true" focusable="false">
        <rect className="av-switch__bg" x="0" y="0" width="52" height="30" rx="15" />
        <circle className="av-switch__knob" cx={checked ? 37 : 15} cy="15" r="11" />
      </svg>
      {!labelFirst && <span>{label}</span>}
    </button>
  );
}
