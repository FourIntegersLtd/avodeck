// With slice C loaded, the header shows "Your profile" and it opens S02.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { signedIn } from "../../test/api";
import { renderAt } from "../../test/render";
import "./index";

test("the header links to Your profile", async () => {
  signedIn();
  renderAt("/today");
  const header = await screen.findByRole("banner");
  expect(await within(header).findByRole("link", { name: "Your profile" })).toHaveAttribute("href", "/profile");
});
