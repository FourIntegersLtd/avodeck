import type { RouteObject } from "react-router-dom";

/** Slice C (case setup) screens. Signed-in routes (profile, new case) and routes inside a case
 * (conflicts, questions). Slice C fills these and registers its screen keys (foundation/lib/screens.ts). */
export const setupRoutes: RouteObject[] = [];
export const setupCaseRoutes: RouteObject[] = [];
