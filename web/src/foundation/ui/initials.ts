/** "Dr Jane Morgan" -> "JM": the first and last names, without a title. */
export function initials(name: string | null | undefined): string {
  const words = (name ?? "").replace(/^(dr|mr|mrs|ms|miss|prof)\.?\s+/i, "").split(/\s+/).filter(Boolean);
  if (!words.length) return "";
  return (words[0]![0]! + (words.length > 1 ? words[words.length - 1]![0]! : "")).toUpperCase();
}
