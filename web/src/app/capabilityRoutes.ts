import type { RouteObject } from "react-router-dom";

/**
 * Every capability's signed-in routes, found by convention so a slice never edits app/routes.tsx:
 * export a `RouteObject[]` named `<name>Routes` from `src/capabilities/<feature>/index.tsx`.
 * Arrays named `<name>CaseRoutes` are routes inside a case (relative paths such as "questions"); they
 * render in the case frame (`cases/:caseId`, CaseLayout) and never at the top level.
 * Arrays named `<name>OfflineRoutes` need no sign-in and no API (rule 4's witness-box exception); they
 * mount outside the signed-in guard.
 * Importing the index also runs its registrations (registerScreen, registerMessages).
 */
type CapabilityModule = Record<string, unknown>;

function collect(modules: Record<string, CapabilityModule>, wanted: (name: string) => boolean): RouteObject[] {
  return Object.keys(modules)
    .sort()
    .flatMap((path) =>
      Object.entries(modules[path] ?? {})
        .filter(([name, value]) => wanted(name) && Array.isArray(value))
        .flatMap(([, value]) => value as RouteObject[]),
    );
}

export function routesFrom(modules: Record<string, CapabilityModule>): RouteObject[] {
  return collect(modules, (name) => /Routes$/.test(name) && !/CaseRoutes$/.test(name) && !/OfflineRoutes$/.test(name));
}

export function offlineRoutesFrom(modules: Record<string, CapabilityModule>): RouteObject[] {
  return collect(modules, (name) => /OfflineRoutes$/.test(name));
}

export function caseRoutesFrom(modules: Record<string, CapabilityModule>): RouteObject[] {
  return collect(modules, (name) => /CaseRoutes$/.test(name));
}

const modules = import.meta.glob<CapabilityModule>("../capabilities/*/index.tsx", { eager: true });

export const capabilityRoutes: RouteObject[] = routesFrom(modules);
export const capabilityCaseRoutes: RouteObject[] = caseRoutesFrom(modules);
export const capabilityOfflineRoutes: RouteObject[] = offlineRoutesFrom(modules);
