// The header's Cases, Your profile and Help links (S03 board) appear once a slice registers the screen
// (foundation/lib/screens.ts keys "cases", "profile", "help"), so the shell never shows a dead link.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { registerScreen } from "../foundation/lib/screens";
import { signedIn } from "../test/api";
import { me } from "../test/fixtures";
import { axeViolations, renderAt } from "../test/render";

test("with no slice registered there is no Cases, profile or Help link", async () => {
  signedIn();
  renderAt("/today");
  const header = await screen.findByRole("banner");
  await within(header).findByRole("link", { name: "Today" });
  for (const name of ["Cases", "Your profile", "Help"]) expect(within(header).queryByRole("link", { name })).toBeNull();
});

test("registered screens get their links in the header", async () => {
  registerScreen("cases", () => "/today#your-cases");
  registerScreen("profile", () => "/profile");
  registerScreen("help", () => "/help");
  signedIn();
  const { container } = renderAt("/today");
  const header = await screen.findByRole("banner");
  expect(await within(header).findByRole("link", { name: "Cases" })).toHaveAttribute("href", "/today#your-cases");
  expect(within(header).getByRole("link", { name: "Your profile" })).toHaveAttribute("href", "/profile");
  expect(within(header).getByRole("link", { name: "Help" })).toHaveAttribute("href", "/help");
  expect(await axeViolations(container)).toEqual([]);
});

test("support staff get a Support console link in the header; everyone else does not", async () => {
  registerScreen("support_console", () => "/support");
  signedIn({ me: { ...me(), is_support_staff: true } });
  const first = renderAt("/today");
  const header = await screen.findByRole("banner");
  const link = await within(header).findByRole("link", { name: "Support console" });
  expect(link).toHaveAttribute("href", "/support");
  expect(await axeViolations(first.container)).toEqual([]);
  first.unmount();

  signedIn();
  renderAt("/today");
  const plain = await screen.findByRole("banner");
  await within(plain).findByRole("link", { name: "Today" });
  expect(within(plain).queryByRole("link", { name: "Support console" })).toBeNull();
});
