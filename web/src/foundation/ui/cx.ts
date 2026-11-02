/** Joins the class names that are set: `cx("a", ok && "a--ok", extra)`. */
export function cx(...names: Array<string | false | null | undefined>): string {
  return names.filter(Boolean).join(" ");
}
