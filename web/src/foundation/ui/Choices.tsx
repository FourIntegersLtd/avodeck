import { useId } from "react";

export interface ChoiceOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface ChoiceProps {
  /** The group's name for assistive technology; shown above the pills unless `hideLabel`. */
  label: string;
  hideLabel?: boolean;
  name?: string;
  value: string | null;
  onChange: (value: string) => void;
  options: ChoiceOption[];
}

const TICK = (
  <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 12.5l4.5 4.5L19 7.5" />
  </svg>
);

/** One group of real radio buttons, so the keyboard and screen readers work without extra code. */
function RadioGroup({ label, hideLabel, name, value, onChange, options, groupClass, optionClass, selectedClass, tick }: ChoiceProps & { groupClass: string; optionClass: string; selectedClass: string; tick: boolean }) {
  const id = useId();
  const labelId = `${id}-label`;
  return (
    <div className="av-choice">
      <span id={labelId} className={hideLabel ? "av-visually-hidden" : "av-choice__label"}>
        {label}
      </span>
      <div role="radiogroup" aria-labelledby={labelId} className={groupClass}>
        {options.map((option) => {
          const selected = option.value === value;
          return (
            <label key={option.value} className={`${optionClass}${selected ? ` ${selectedClass}` : ""}`}>
              <input
                type="radio"
                className="av-visually-hidden"
                name={name ?? id}
                value={option.value}
                checked={selected}
                disabled={option.disabled}
                onChange={() => onChange(option.value)}
              />
              {tick && selected && TICK}
              <span>{option.label}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}

/** Large pill choices (one answer): a tick marks the chosen pill. The amber variant is for things to look at. */
export function ChoicePills({ tone, ...props }: ChoiceProps & { tone?: "amber" }) {
  return <RadioGroup {...props} groupClass={`av-pill-group${tone === "amber" ? " av-pill-group--amber" : ""}`} optionClass="av-pill" selectedClass="av-pill--selected" tick />;
}

/** A small switch between views of the same thing (list or lanes). */
export function Segmented(props: ChoiceProps) {
  return <RadioGroup {...props} groupClass="av-segmented" optionClass="av-segmented__option" selectedClass="av-segmented__option--selected" tick={false} />;
}
