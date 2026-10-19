import type { RouteObject } from "react-router-dom";
import { registerScreen } from "../../foundation/lib/screens";
import { Help } from "./Help";
import { Today } from "./Today";

export const todayRoutes: RouteObject[] = [
  { path: "today", element: <Today /> },
  { path: "help", element: <Help /> },
];

// The header's Cases and Help links (S03 board).
registerScreen("cases", () => "/today#your-cases");
registerScreen("help", () => "/help");
