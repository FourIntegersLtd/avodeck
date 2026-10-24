// The support account's frame: Support console, Help and Account only, and a band that says on every
// signed-in screen (case screens too) that this is the Avodeck support account, not an expert's.
import { render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import { registerScreen, resetScreens } from "../lib/screens";
import { signedIn } from "../../test/api";
import { me } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { AppShell } from "./AppShell";

afterEach(() => resetScreens());

test("support staff see Support console, Help and Account; no Today, Cases, profile or Messages", async () => {
  registerScreen("cases", () => "/today#your-cases");
  registerScreen("profile", () => "/profile");
  registerScreen("help", () => "/help");
  registerScreen("support_console", () => "/support");
  signedIn({ me: { ...me(), is_support_staff: true } });
  const { container } = renderAt("/support");
  const header = await screen.findByRole("banner");
  const links = await within(header).findAllByRole("link");
  const names = links.map((a) => a.getAttribute("aria-label") ?? a.textContent ?? "");
  expect(names.filter((n) => !/home page/.test(n))).toEqual(["Support console", "Help", expect.stringMatching(/Account/)]);
  for (const name of ["Today", "Cases", "Your profile", "Messages", "New case"]) {
    expect(within(header).queryByRole("link", { name })).toBeNull();
  }
  expect(within(header).getByText("Avodeck support account")).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
});

test("an expert never sees the support band", async () => {
  signedIn();
  renderAt("/today");
  await screen.findByRole("banner");
  expect(screen.queryByText("Avodeck support account")).toBeNull();
});

test("the band shows on a case screen too, where the case frame has its own bar", () => {
  const router = createMemoryRouter(
    [{ path: "/cases/:caseId/*", element: <AppShell support name="Support Person"><p>case</p></AppShell> }],
    { initialEntries: ["/cases/abc/read"] },
  );
  render(<RouterProvider router={router} />);
  expect(screen.getByText("Avodeck support account")).toBeInTheDocument();
});

test("the band is large type, not small print", () => {
  const css = readFileSync(join(__dirname, "styles.css"), "utf8");
  const rule = css.match(/\.av-shell__support-band\s*\{([^}]*)\}/)?.[1] ?? "";
  expect(rule).toMatch(/font-size:\s*var\(--text-body\)/);
  expect(rule).toMatch(/font-weight:\s*700/);
});
