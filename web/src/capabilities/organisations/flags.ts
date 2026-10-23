// Feature flags for parts of slice O that wait on an API route. A flag is off unless the build turns it on
// (VITE_FLAG_<NAME>=1); tests use setFlag. Off means the button or form is not drawn at all.
export type Flag = "replacement_case" | "support_custody_transfer";

const overrides: Partial<Record<Flag, boolean>> = {};

export function setFlag(name: Flag, on: boolean): void {
  overrides[name] = on;
}

export function flag(name: Flag): boolean {
  return overrides[name] ?? import.meta.env[`VITE_FLAG_${name.toUpperCase()}`] === "1";
}
