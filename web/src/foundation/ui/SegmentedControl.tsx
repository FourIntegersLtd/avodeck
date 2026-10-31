export interface SegmentedOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface SegmentedControlProps {
  /** The group's name for assistive technology. */
  label: string;
  value: string | null;
  onChange: (value: string) => void;
  options: SegmentedOption[];
}

/** Two to five choices of one kind: a grey track with the chosen one raised in white. Real buttons with `aria-pressed`. */
export function SegmentedControl({ label, value, onChange, options }: SegmentedControlProps) {
  return (
    <div role="group" aria-label={label} className="av-segmented-control">
      {options.map((o) => (
        <button key={o.value} type="button" className="av-segmented-control__option" aria-pressed={o.value === value} disabled={o.disabled} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
