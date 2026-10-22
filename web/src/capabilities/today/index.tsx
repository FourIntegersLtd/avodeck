import type { RouteObject } from "react-router-dom";
import { registerNeedsYouIcon, type NeedsYouIcon } from "../../foundation/lib/needsYouIcons";
import { registerScreen } from "../../foundation/lib/screens";
import "./today.css";
import { Help } from "./Help";
import { Today } from "./Today";

export const todayRoutes: RouteObject[] = [
  { path: "today", element: <Today /> },
  { path: "help", element: <Help /> },
];

// The header's Cases and Help links (S03 board).
registerScreen("cases", () => "/today#your-cases");
registerScreen("help", () => "/help");

// The icon of each kind of "needs you" row (S03 board). A slice can register its own kind again to change it.
const rowIcons: Record<string, NeedsYouIcon> = {
  after_serving_written_questions_due: { glyph: "clock", tone: "amber" },
  after_serving_new_pages: { glyph: "page", tone: "mint" },
  after_serving_opposing_to_check: { glyph: "checklist", tone: "amber" },
  reader_pages_to_read: { glyph: "book", tone: "mint" },
  reader_pages_to_type: { glyph: "pen", tone: "mint" },
  setup_questions_to_confirm: { glyph: "question", tone: "amber" },
  setup_profile_incomplete: { glyph: "checklist", tone: "amber" },
  trial_joint_statement_to_sign: { glyph: "pen", tone: "amber" },
  trial_pages_to_confirm: { glyph: "checklist", tone: "amber" },
  understand_things_to_look_at: { glyph: "checklist", tone: "amber" },
};
for (const [kind, icon] of Object.entries(rowIcons)) registerNeedsYouIcon(kind, icon);
