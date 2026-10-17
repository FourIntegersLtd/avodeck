import type { RouteObject } from "react-router-dom";

/**
 * Every capability's signed-in routes, found by convention so a slice never edits app/routes.tsx:
 * export a `RouteObject[]` named `<name>Routes` from `src/capabilities/<feature>/index.tsx`.
 * Importing the index also runs its registrations (registerScreen, registerMessages).
 */
type CapabilityModule = Record<string, unknown>;

export function routesFrom(modules: Record<string, CapabilityModule>): RouteObject[] {
  return Object.keys(modules)
    .sort()
    .flatMap((path) =>
      Object.entries(modules[path] ?? {})
        .filter(([name, value]) => /Routes$/.test(name) && Array.isArray(value))
        .flatMap(([, value]) => value as RouteObject[]),
    );
}

const modules = import.meta.glob<CapabilityModule>("../capabilities/*/index.tsx", { eager: true });

export const capabilityRoutes: RouteObject[] = routesFrom(modules);
