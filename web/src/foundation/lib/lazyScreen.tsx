import type { ReactElement } from "react";
import type { RouteObject } from "react-router-dom";

/** A route whose screen downloads the first time it is opened (react-router's `lazy`). The router waits
 * for the download before it changes page, so the old screen stays up and no half-built page shows. */
export function lazyScreen<M>(load: () => Promise<M>, render: (module: M) => ReactElement): Pick<RouteObject, "lazy"> {
  return { lazy: async () => ({ element: render(await load()) }) };
}
