// B2-2: one menu on the Radix dropdown primitive (menu roles, roving focus, type-ahead).
import { screen, within } from "@testing-library/react";
import { useState } from "react";
import { expect, test, vi } from "vitest";
import { axeViolations, renderUi } from "../../test/render";
import { Menu, MenuItem, MenuSection } from "./Menu";

function Sample({ onPick = () => undefined }: { onPick?: () => void }) {
  return (
    <Menu label="View" trigger={<>{"View"}</>}>
      <MenuSection label="Size">
        <MenuItem pressed onClick={onPick}>{"Fit to width"}</MenuItem>
        <MenuItem pressed={false}>{"Whole page"}</MenuItem>
      </MenuSection>
      <MenuSection>
        <MenuItem>{"Turn left"}</MenuItem>
        <MenuItem disabled>{"Undo turn"}</MenuItem>
        <MenuItem shortcut="?">{"Keys"}</MenuItem>
      </MenuSection>
    </Menu>
  );
}

test("the trigger opens a role=menu named by its label, with menuitem rows and ticked checkbox rows", async () => {
  const { user } = renderUi(<Sample />);
  const trigger = screen.getByRole("button", { name: "View" });
  expect(trigger).toHaveAttribute("aria-haspopup", "menu");
  await user.click(trigger);
  const menu = await screen.findByRole("menu", { name: "View" });
  expect(within(menu).getByRole("menuitemcheckbox", { name: "Fit to width" })).toHaveAttribute("aria-checked", "true");
  expect(within(menu).getByRole("menuitemcheckbox", { name: "Whole page" })).toHaveAttribute("aria-checked", "false");
  expect(within(menu).getAllByRole("menuitem").map((i) => i.textContent)).toEqual(["Turn left", "Undo turn", "Keys?"]);
  expect(await axeViolations(document.body)).toEqual([]);
});

test("Home and End jump to the first and last row; arrows move; type-ahead finds a row", async () => {
  const { user } = renderUi(<Sample />);
  await user.click(screen.getByRole("button", { name: "View" }));
  const menu = await screen.findByRole("menu");
  await user.keyboard("{End}");
  expect(within(menu).getByRole("menuitem", { name: /Keys/ })).toHaveFocus();
  await user.keyboard("{Home}");
  expect(within(menu).getByRole("menuitemcheckbox", { name: "Fit to width" })).toHaveFocus();
  await user.keyboard("{ArrowDown}");
  expect(within(menu).getByRole("menuitemcheckbox", { name: "Whole page" })).toHaveFocus();
  await user.keyboard("t");
  expect(within(menu).getByRole("menuitem", { name: "Turn left" })).toHaveFocus();
});

test("choosing a row runs it and closes the menu; Tab closes it; Escape closes it and returns focus", async () => {
  const onPick = vi.fn();
  const { user } = renderUi(<Sample onPick={onPick} />);
  const trigger = screen.getByRole("button", { name: "View" });
  await user.click(trigger);
  await user.click(await screen.findByRole("menuitemcheckbox", { name: "Fit to width" }));
  expect(onPick).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("menu")).toBeNull();
  await user.click(trigger);
  await screen.findByRole("menu");
  await user.tab();
  expect(screen.queryByRole("menu")).toBeNull();
  await user.click(trigger);
  await screen.findByRole("menu");
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();
  expect(trigger).toHaveFocus();
});

test("a caller can control it and give the trigger its own id and look", async () => {
  function Controlled() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <output>{open ? "open" : "shut"}</output>
        <Menu label="More" trigger={<>{"More"}</>} open={open} onOpenChange={setOpen} triggerId="the-trigger" triggerClassName="av-button av-button--secondary">
          <MenuItem>{"One"}</MenuItem>
          <MenuItem keepOpen>{"Stay"}</MenuItem>
        </Menu>
      </>
    );
  }
  const { user } = renderUi(<Controlled />);
  const trigger = screen.getByRole("button", { name: "More" });
  expect(trigger.id).toBe("the-trigger");
  expect(trigger).toHaveClass("av-button");
  await user.click(trigger);
  expect(screen.getByRole("status")).toHaveTextContent("open");
  await user.click(await screen.findByRole("menuitem", { name: "Stay" }));
  expect(screen.getByRole("status")).toHaveTextContent("open");
  await user.click(screen.getByRole("menuitem", { name: "One" }));
  expect(screen.getByRole("status")).toHaveTextContent("shut");
});
