import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, test } from "vitest";
import { registerScreen, resetScreens } from "../lib/screens";
import { renderUi } from "../../test/render";
import { fixturePack, launchPack } from "../../test/fixtures";
import { CaseHeader } from ".";

afterEach(() => resetScreens());

test("a step pill opens the first screen inside this case, never a case-less form", () => {
  registerScreen("new_case", () => "/cases/new");
  registerScreen("conflicts", ({ caseId }) => `/cases/${caseId}/conflicts`);
  renderUi(
    <MemoryRouter>
      <CaseHeader bar caseId="c1" steps={[{ key: "prepare", screens: ["new_case", "conflicts"] }]} current="prepare" />
    </MemoryRouter>,
  );
  expect(screen.getByRole("link", { name: /Prepare/ })).toHaveAttribute("href", "/cases/c1/conflicts");
});

// Reason: the case bar's action links moved into a disclosure menu on the avatar.
const ACTIONS = [{ key: "a", labelKey: "app.nav.profile", to: "/cases/c1/team" }];
const bar = (actions?: typeof ACTIONS) => (
  <MemoryRouter>
    <CaseHeader bar caseId="c1" steps={[]} person="Jane Morgan" actions={actions} />
  </MemoryRouter>
);

test("with actions the avatar is a disclosure button; the bar has no action links", async () => {
  const user = userEvent.setup();
  renderUi(bar(ACTIONS));
  expect(screen.queryByRole("link", { name: "Your profile" })).toBeNull();
  const button = screen.getByRole("button", { name: "Your account" });
  expect(button).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("link", { name: "Case settings" })).toBeNull();
  await user.click(button);
  expect(button).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("link", { name: "Your account" })).toHaveAttribute("href", "/account");
  expect(screen.getByRole("link", { name: "Case settings" })).toHaveAttribute("href", "/cases/c1/team");
});

test("Escape and an outside click close the menu and Escape returns focus to the button", async () => {
  const user = userEvent.setup();
  renderUi(bar(ACTIONS));
  const button = screen.getByRole("button", { name: "Your account" });
  await user.click(button);
  await user.keyboard("{Escape}");
  expect(button).toHaveAttribute("aria-expanded", "false");
  expect(button).toHaveFocus();
  await user.click(button);
  await user.click(document.body);
  expect(button).toHaveAttribute("aria-expanded", "false");
});

test("the menu opens from the keyboard", async () => {
  const user = userEvent.setup();
  renderUi(bar(ACTIONS));
  screen.getByRole("button", { name: "Your account" }).focus();
  await user.keyboard("{Enter}");
  expect(screen.getByRole("button", { name: "Your account" })).toHaveAttribute("aria-expanded", "true");
});

test("without actions the avatar stays a plain link to the account", () => {
  renderUi(bar());
  expect(screen.getByRole("link", { name: "Account" })).toHaveAttribute("href", "/account");
  expect(screen.queryByRole("button")).toBeNull();
});

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("the step's screens row under the $name", ({ pack }) => {
  const steps = pack.workflow_steps as Array<{ key: string; screens?: string[] }>;
  const step = steps.find((s) => (s.screens ?? []).length >= 3)!;
  const screens = step.screens!;

  function register(skip: string[] = []) {
    for (const key of screens) {
      if (skip.includes(key)) continue;
      registerScreen(key, ({ caseId }) => `/cases/${caseId}/${key}`);
    }
  }
  const header = (screenKey?: string) => (
    <MemoryRouter>
      <CaseHeader bar caseId="c1" steps={steps} current={step.key} screen={screenKey} />
    </MemoryRouter>
  );

  test("lists every reachable screen of the current step and marks the current one", () => {
    register();
    renderUi(header(screens[1]));
    const row = screen.getByRole("navigation", { name: "Screens in this step" });
    const links = within(row).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(screens.map((k) => `/cases/c1/${k}`));
    expect(links.filter((a) => a.getAttribute("aria-current") === "page")).toHaveLength(1);
    expect(links[1]).toHaveAttribute("aria-current", "page");
  });

  test("leaves out screens outside the case and screens that need an id", () => {
    register(screens.slice(0, 2));
    registerScreen(screens[0]!, () => "/cases/new");
    registerScreen(screens[1]!, ({ caseId, params }) => `/cases/${caseId}/thing/${String(params.id ?? "")}`);
    renderUi(header(screens[2]));
    const links = screen.queryByRole("navigation", { name: "Screens in this step" });
    const hrefs = links ? within(links).queryAllByRole("link").map((a) => a.getAttribute("href")) : [];
    expect(hrefs).not.toContain("/cases/new");
    expect(hrefs.some((h) => h?.endsWith("/thing/"))).toBe(false);
  });

  test("a step with one reachable screen has no row", () => {
    registerScreen(screens[0]!, ({ caseId }) => `/cases/${caseId}/only`);
    renderUi(header(screens[0]));
    expect(screen.queryByRole("navigation", { name: "Screens in this step" })).toBeNull();
  });
});
