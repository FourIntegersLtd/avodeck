// The signed-in frame (S03 header): the bell with the unread count, the person's initials for their
// account, the banner when we couldn't email them (F33), and the header nav that collapses into a
// hamburger + drawer below the mobile breakpoint.
import { screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, expect, test } from "vitest";
import { registerScreen, resetScreens } from "../lib/screens";
import { signedIn } from "../../test/api";
import { message, messages } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";

test("the bell shows how many messages are unread and opens them", async () => {
  signedIn({ messages: messages({ items: [message(), message({ id: "99999999-9999-4999-8999-999999999999" })], unread_count: 2 }) });
  const { container } = renderAt("/today");
  const header = await screen.findByRole("banner");
  const bell = await within(header).findByRole("link", { name: "Messages, 2 unread" });
  expect(bell).toHaveAttribute("href", "/messages");
  expect(await axeViolations(container)).toEqual([]);
});

test("with nothing unread the bell just says Messages", async () => {
  signedIn();
  renderAt("/today");
  const header = await screen.findByRole("banner");
  expect(await within(header).findByRole("link", { name: "Messages" })).toHaveAttribute("href", "/messages");
});

test("the person's initials open their account", async () => {
  signedIn();
  renderAt("/today");
  const header = await screen.findByRole("banner");
  const avatar = within(header).getByRole("link", { name: "Account" });
  expect(avatar).toHaveAttribute("href", "/account");
  expect(avatar).toHaveTextContent("JM");
});

test("a bounced email address shows a banner on every signed-in screen", async () => {
  signedIn({ messages: messages({ email_bounced: true }) });
  renderAt("/account");
  expect(await screen.findByText("We couldn't email you. Check your address.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Check your email address" })).toHaveAttribute("href", "/account");
});

test("the logo goes back to the Avodeck home page", async () => {
  signedIn();
  renderAt("/today");
  const header = await screen.findByRole("banner");
  expect(within(header).getByRole("link", { name: "Avodeck home page" })).toHaveAttribute("href", "/");
});

afterEach(() => resetScreens());

test("the right of the bar reads bell, Help, then the person's initials", async () => {
  registerScreen("help", () => "/help");
  signedIn();
  renderAt("/today");
  const header = await screen.findByRole("banner");
  await within(header).findByRole("link", { name: "Messages" });
  const names = within(header)
    .getAllByRole("link")
    .map((a) => a.getAttribute("aria-label") ?? a.textContent);
  const tail = names.slice(-3);
  expect(tail[0]).toBe("Messages");
  expect(tail[1]).toBe("Help");
  expect(tail[2]).toMatch(/JM/);
});

test("the unread badge is amber, inactive places are muted and the avatar has a 52px tap area", () => {
  const css = readFileSync(join(__dirname, "styles.css"), "utf8");
  expect(css.match(/\.av-shell__count\s*\{([^}]*)\}/)?.[1]).toMatch(/background:\s*var\(--amber-bright\)/);
  expect(css.match(/\.av-shell__nav a\s*\{([^}]*)\}/)?.[1]).toMatch(/color:\s*var\(--ink-soft\)/);
  expect(css.match(/\.av-shell__avatar\s*\{([^}]*)\}/)?.[1]).toMatch(/min-height:\s*var\(--button-min-height\)/);
});

test("the logo is a 40px mark with the name drawn as SVG text", async () => {
  signedIn();
  renderAt("/today");
  const header = await screen.findByRole("banner");
  const logo = within(header).getByRole("link", { name: "Avodeck home page" });
  expect(logo.querySelector("svg")!.getAttribute("height")).toBe("40");
  expect(logo.querySelector("svg text")).not.toBeNull();
});

test("the header's nav collapses into a hamburger below 768px, and shows as links above it", () => {
  const css = readFileSync(join(__dirname, "styles.css"), "utf8");
  // Base state: the header's own nav is visible, the hamburger is hidden.
  expect(css.match(/\.av-shell__hamburger\s*\{([^}]*)\}/)?.[1]).toMatch(/display:\s*none/);
  // Below the breakpoint: the header's nav hides and the hamburger (and its drawer) take over.
  const narrow = css.match(/@media \(max-width:\s*768px\)\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";
  expect(narrow).toMatch(/\.av-shell__header \.av-shell__nav\s*\{\s*display:\s*none/);
  expect(narrow).toMatch(/\.av-shell__hamburger\s*\{\s*display:\s*inline-flex/);
});

test("the hamburger and the drawer's close button meet the 52px tap target", () => {
  const css = readFileSync(join(__dirname, "styles.css"), "utf8");
  expect(css.match(/\.av-shell__hamburger\s*\{([^}]*)\}/)?.[1]).toMatch(/min-height:\s*var\(--button-min-height\)/);
  expect(css.match(/\.av-shell__drawer-close\s*\{([^}]*)\}/)?.[1]).toMatch(/min-height:\s*var\(--button-min-height\)/);
});

test("the hamburger opens the drawer with the same places as the header nav, and closes on Escape", async () => {
  registerScreen("cases", () => "/today#your-cases");
  signedIn();
  const { user } = renderAt("/today");
  const hamburger = await screen.findByRole("button", { name: "Open menu" });
  expect(hamburger).toHaveAttribute("aria-expanded", "false");
  await user.click(hamburger);
  const dialog = await screen.findByRole("dialog", { name: "Menu" });
  expect(hamburger).toHaveAttribute("aria-expanded", "true");
  expect(await within(dialog).findByRole("link", { name: "Today" })).toHaveAttribute("aria-current", "page");
  expect(within(dialog).getByRole("link", { name: "Cases" })).toHaveAttribute("href", "/today#your-cases");
  // Focus moved into the drawer.
  expect(dialog.contains(document.activeElement)).toBe(true);
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "Menu" })).toBeNull();
  // Focus came back to the hamburger.
  expect(document.activeElement).toBe(hamburger);
  expect(hamburger).toHaveAttribute("aria-expanded", "false");
});

test("clicking the drawer's backdrop or its close button closes it, without going anywhere", async () => {
  signedIn();
  const { user } = renderAt("/today");
  const hamburger = await screen.findByRole("button", { name: "Open menu" });
  await user.click(hamburger);
  const dialog = await screen.findByRole("dialog", { name: "Menu" });
  await user.click(within(dialog).getByRole("button", { name: "Close menu" }));
  expect(screen.queryByRole("dialog", { name: "Menu" })).toBeNull();
  await user.click(hamburger);
  await screen.findByRole("dialog", { name: "Menu" });
  const scrim = document.querySelector(".av-shell__drawer-scrim")!;
  await user.click(scrim);
  expect(screen.queryByRole("dialog", { name: "Menu" })).toBeNull();
  // Still on /today: the page heading is there, closing the drawer didn't navigate away.
  expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
});

test("following a place in the drawer closes it", async () => {
  signedIn();
  const { user } = renderAt("/today");
  const hamburger = await screen.findByRole("button", { name: "Open menu" });
  await user.click(hamburger);
  const dialog = await screen.findByRole("dialog", { name: "Menu" });
  await user.click(within(dialog).getByRole("link", { name: "Today" }));
  expect(screen.queryByRole("dialog", { name: "Menu" })).toBeNull();
});
