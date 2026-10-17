import type { InputHTMLAttributes } from "react";

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label: string;
}

export function Checkbox({ label, ...input }: CheckboxProps) {
  return (
    <label className="av-checkbox">
      <input type="checkbox" {...input} />
      <span>{label}</span>
    </label>
  );
}
