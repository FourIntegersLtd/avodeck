import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import type { ReactElement } from "react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AppProviders } from "../app/AppProviders";
import { routes } from "../app/routes";

/** Renders the whole app at `path` with an in-memory router. */
export function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const user = userEvent.setup();
  const view = render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return { ...view, router, user };
}

export function renderUi(ui: ReactElement) {
  const user = userEvent.setup();
  return { ...render(<AppProviders>{ui}</AppProviders>), user };
}

/** WCAG 2.2 AA rules that jsdom can evaluate (colour contrast is checked in Playwright). */
export async function axeViolations(container: Element) {
  const result = await axe.run(container, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
    rules: { "color-contrast": { enabled: false } },
  });
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}
