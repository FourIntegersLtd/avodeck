// B2-1: one dialog primitive; the page behind is unreachable, focus is trapped and returned.
import { screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { expect, test } from "vitest";
import { axeViolations, renderUi } from "../../test/render";
import { Dialog } from "./Dialog";

function Host() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <main>
        <button type="button" onClick={() => setOpen(true)}>{"Open it"}</button>
      </main>
      {open && (
        <Dialog title="Sure?" onClose={() => setOpen(false)}>
          <button type="button">{"First"}</button>
          <button type="button">{"Last"}</button>
        </Dialog>
      )}
    </>
  );
}

test("the page behind a dialog is hidden from screen readers and body scroll is locked", async () => {
  const { user, container } = renderUi(<Host />);
  await user.click(screen.getByRole("button", { name: "Open it" }));
  const dialog = await screen.findByRole("dialog", { name: "Sure?" });
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(container.querySelector("main")?.closest("[aria-hidden='true']")).not.toBeNull();
  expect(document.body.hasAttribute("data-scroll-locked")).toBe(true);
  expect(await axeViolations(document.body)).toEqual([]);
});

test("Tab goes round the dialog and Escape closes it, giving focus back to the trigger", async () => {
  const { user } = renderUi(<Host />);
  const trigger = screen.getByRole("button", { name: "Open it" });
  await user.click(trigger);
  const dialog = await screen.findByRole("dialog", { name: "Sure?" });
  expect(dialog.contains(document.activeElement)).toBe(true);
  for (let i = 0; i < 5; i++) {
    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
  }
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  await waitFor(() => expect(trigger).toHaveFocus());
  expect(document.body.hasAttribute("data-scroll-locked")).toBe(false);
});

test("Escape inside a dialog is marked handled so a panel behind does not also close", async () => {
  const { user } = renderUi(<Host />);
  await user.click(screen.getByRole("button", { name: "Open it" }));
  await screen.findByRole("dialog");
  let prevented: boolean | undefined;
  document.addEventListener("keydown", (e) => (prevented = e.defaultPrevented));
  await user.keyboard("{Escape}");
  expect(prevented).toBe(true);
});
