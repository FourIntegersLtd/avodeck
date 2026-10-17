import type { RouteObject } from "react-router-dom";
import { Today } from "./Today";

export const todayRoutes: RouteObject[] = [{ path: "today", element: <Today /> }];
