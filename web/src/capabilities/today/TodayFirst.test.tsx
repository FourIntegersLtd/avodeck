// S03d Today (first visit): a welcome, what to do first, and how a case works. No pack words.
import { screen, within } from "@testing-library/react";
import { beforeAll, expect, test } from "vitest";
import { registerScreen } from "../../foundation/lib/screens";
import { signedIn } from "../../test/api";
import { me, today } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";

beforeAll(() => {
  registerScreen("new_case", () => "/cases/new");
  registerScreen("profile", () => "/profile");
});

const empty = today({ cases: [], needs_you: [], closed_count: 0 });

function setup(opts: { done?: boolean; signature?: boolean } = {}) {
  // The server says which steps are done and how many; the page asks nothing else and counts nothing.
  const profile = Boolean(opts.done);
  const signature = Boolean(opts.signature);
  const done_steps = 1 + Number(profile) + Number(signature);
  signedIn({
    me: me({ greeting_name: "Dr Morgan" }),
    today: { ...empty, readiness: { account: true, profile, signature, done_steps, total_steps: 3 } },
  });
}

test("welcomes a person with no cases and passes axe", async () => {
  setup();
  const { container } = renderAt("/today");
  expect(await screen.findByRole("heading", { level: 1, name: "Welcome to Avodeck, Dr Morgan" })).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
});

test("getting ready: the account is done; profile and signature are still to do", async () => {
  setup();
  renderAt("/today");
  const list = await screen.findByRole("region", { name: "Getting ready" });
  expect(await within(list).findByText("1 of 3 done")).toBeInTheDocument();
  expect(within(list).getByRole("link", { name: "Fill it in" })).toHaveAttribute("href", "/profile");
  expect(within(list).getByRole("link", { name: "Add it" })).toHaveAttribute("href", "/profile");
});

test("a finished profile and signature are ticked off", async () => {
  setup({ done: true, signature: true });
  renderAt("/today");
  const list = await screen.findByRole("region", { name: "Getting ready" });
  expect(await within(list).findByText("3 of 3 done")).toBeInTheDocument();
  expect(within(list).queryByRole("link", { name: "Fill it in" })).toBeNull();
});

test("Start your first case is the one primary button", async () => {
  setup();
  const { container } = renderAt("/today");
  expect(await screen.findByRole("link", { name: "Start your first case" })).toHaveAttribute("href", "/cases/new");
  expect([...container.querySelectorAll(".av-button")].filter((el) => ![...el.classList].some((c) => /^av-button--/.test(c)))).toHaveLength(1);
});

test("have these to hand, need a hand, and five steps for how a case works", async () => {
  setup();
  renderAt("/today");
  expect(await screen.findByRole("heading", { name: "Have these to hand" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Need a hand?" })).toBeInTheDocument();
  const how = screen.getByRole("region", { name: "How a case works" });
  expect(within(how).getAllByRole("listitem")).toHaveLength(5);
});
