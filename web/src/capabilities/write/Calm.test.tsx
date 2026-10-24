// The calm look for screens 14 and 15: one green button, choices of one kind as a segmented control,
// rare actions in a menu.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { Q1, serve, sides, views } from "./testing";
import { WRITE_MESSAGES as M } from "./words";

const primaries = (root: HTMLElement) => root.querySelectorAll(".av-button:not([class*='av-button--'])");

test("screen 14 shows exactly one primary button", async () => {
  serve("launch");
  const { container } = renderAt(`/cases/${CASE_A}/points?q=${Q1}`);
  await screen.findByRole("region", { name: sides("launch")[0]!.label });
  expect(primaries(container)).toHaveLength(1);
  expect(primaries(container)[0]).toHaveTextContent(M["write.points.write"]!);
});

test("screen 14 keeps moving and removing a point in a menu", async () => {
  serve("launch");
  const { user } = renderAt(`/cases/${CASE_A}/points?q=${Q1}`);
  const column = await screen.findByRole("region", { name: sides("launch")[0]!.label });
  expect(within(column).queryByRole("button", { name: M["write.points.remove"]! })).toBeNull();
  await user.click(within(column).getByRole("button", { name: M["write.points.more"]! }));
  expect(await screen.findByRole("button", { name: M["write.points.remove"]! })).toBeInTheDocument();
});

test("screen 15 shows exactly one primary button and the views as a segmented control", async () => {
  serve("launch");
  const { container } = renderAt(`/cases/${CASE_A}/opinion?q=${Q1}`);
  const group = await screen.findByRole("group", { name: M["write.opinion.view"]! });
  for (const v of views("launch")) expect(within(group).getByRole("button", { name: v.label })).toHaveAttribute("aria-pressed");
  expect(primaries(container)).toHaveLength(1);
});
