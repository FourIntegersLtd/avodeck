// The signed-in frame (S03 header): the bell with the unread count, the person's initials for their
// account, and the banner when we couldn't email them (F33).
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
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
  expect(within(header).getByRole("link", { name: "Avodeck home page" })).toHaveAttribute("href", "http://localhost:3000");
});
