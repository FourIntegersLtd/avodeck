// The 412 choice any form can use: when another window saved first, show both versions and let the
// person keep what they typed or take the saved one. Typed work is never thrown away.
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { renderUi } from "../../test/render";
import { t } from "../i18n";
import { StaleChoice } from "./StaleChoice";

test("shows the typed draft and the saved value, and each button calls back with what it needs", async () => {
  const onKeepMine = vi.fn();
  const onUseSaved = vi.fn();
  const { user } = renderUi(
    <StaleChoice draft="What I typed" saved="From the other window" savedVersion={3} onKeepMine={onKeepMine} onUseSaved={onUseSaved} />,
  );
  const dialog = screen.getByRole("dialog", { name: t("stale.title") });
  expect(within(dialog).getByText("What I typed")).toBeInTheDocument();
  expect(within(dialog).getByText("From the other window")).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: t("stale.keep_mine") }));
  expect(onKeepMine).toHaveBeenCalledWith(3);
  await user.click(within(dialog).getByRole("button", { name: t("stale.use_saved") }));
  expect(onUseSaved).toHaveBeenCalledWith("From the other window", 3);
});

test("a form's own words and its own way of showing a value are used", async () => {
  renderUi(
    <StaleChoice
      draft={{ text: "mine" }}
      saved={{ text: "theirs" }}
      savedVersion={2}
      show={(v) => `value: ${v.text}`}
      words={{ title: "Pick one", keepMine: "Mine please", useSaved: "Theirs please" }}
      onKeepMine={() => {}}
      onUseSaved={() => {}}
    />,
  );
  const dialog = screen.getByRole("dialog", { name: "Pick one" });
  expect(within(dialog).getByText("value: mine")).toBeInTheDocument();
  expect(within(dialog).getByText("value: theirs")).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Mine please" })).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Theirs please" })).toBeInTheDocument();
});

test("with no saved value from the server, keeping the saved one still closes without losing the draft", async () => {
  const onUseSaved = vi.fn();
  const { user } = renderUi(<StaleChoice draft="What I typed" saved={null} onKeepMine={() => {}} onUseSaved={onUseSaved} />);
  expect(screen.getByText("What I typed")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: t("stale.use_saved") }));
  expect(onUseSaved).toHaveBeenCalledWith(null, 0);
});

test("closing the box counts as neither choice", async () => {
  const onClose = vi.fn();
  const onKeepMine = vi.fn();
  const { user } = renderUi(<StaleChoice draft="a" saved="b" onKeepMine={onKeepMine} onUseSaved={() => {}} onClose={onClose} />);
  await user.keyboard("{Escape}");
  expect(onClose).toHaveBeenCalled();
  expect(onKeepMine).not.toHaveBeenCalled();
});
