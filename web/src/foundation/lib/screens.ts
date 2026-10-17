// Screen registry: slices register where their screens live, so Today's "needs you" items and
// buttons can open them without the foundation knowing any slice (architecture 8.4).
export interface ScreenTarget {
  caseId?: string | null;
  params?: Record<string, unknown>;
}

type PathBuilder = (target: { caseId: string; params: Record<string, unknown> }) => string;

const screens = new Map<string, PathBuilder>();

export function registerScreen(key: string, path: PathBuilder): void {
  screens.set(key, path);
}

export function hasScreen(key: string): boolean {
  return screens.has(key);
}

/** The address of a registered screen, or null when no slice has registered it yet. */
export function screenPath(key: string, target: ScreenTarget = {}): string | null {
  const build = screens.get(key);
  return build ? build({ caseId: target.caseId ?? "", params: target.params ?? {} }) : null;
}
