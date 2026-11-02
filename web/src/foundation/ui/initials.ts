import { t } from "../i18n";

/** "Dr Jane Morgan" -> "JM": the first and last names, without a title. The titles are locale words (`name.titles`). */
export function initials(name: string | null | undefined): string {
  const titles = t("name.titles", {}, "").split("|").filter(Boolean);
  let words = (name ?? "").trim().split(/\s+/).filter(Boolean);
  if (words.length > 1 && titles.includes(words[0]!.replace(/\.$/, "").toLowerCase())) words = words.slice(1);
  if (!words.length) return "";
  return (words[0]![0]! + (words.length > 1 ? words[words.length - 1]![0]! : "")).toUpperCase();
}
