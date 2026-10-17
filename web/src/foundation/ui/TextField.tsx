import { useId, type InputHTMLAttributes } from "react";

export interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: string;
  name: string;
  hint?: string;
  error?: string;
  code?: boolean;
}

/** A labelled input; its hint and error are read out with it. */
export function TextField({ label, hint, error, code, className, ...input }: TextFieldProps) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <div className="av-field">
      <label className="av-field__label" htmlFor={id}>
        {label}
      </label>
      {hint && (
        <p className="av-field__hint" id={hintId}>
          {hint}
        </p>
      )}
      {error && (
        <p className="av-field__error" id={errorId}>
          {error}
        </p>
      )}
      <input
        id={id}
        className={["av-input", code && "av-input--code", className].filter(Boolean).join(" ")}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        {...input}
      />
    </div>
  );
}

export interface CodeFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  autoFocus?: boolean;
}

/** The 6-digit code from the authenticator app. */
export function CodeField({ label, value, onChange, error, autoFocus }: CodeFieldProps) {
  return (
    <TextField
      label={label}
      name="code"
      code
      inputMode="numeric"
      autoComplete="one-time-code"
      maxLength={6}
      pattern="[0-9]*"
      required
      autoFocus={autoFocus}
      value={value}
      error={error}
      onChange={(e) => onChange(e.target.value.replace(/\D/g, ""))}
    />
  );
}
