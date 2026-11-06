// One confirmation box for the whole app: it replaces the browser's own confirm() with two buttons.
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { renderUi } from "../../test/render";
import { useConfirm } from "./confirm";

function Host({ onAnswer, danger }: { onAnswer: (ok: boolean) => void; danger?: boolean }) {
  const confirm = useConfirm();
  return (
    <button type="button" onClick={async () => onAnswer(await confirm({ title: "Remove it?", message: "This cannot be undone.", danger }))}>
      {"Ask"}
    </button>
  );
}

test("asks with the title and words it was given, and Confirm answers yes", async () => {
  const answers: boolean[] = [];
  const { user } = renderUi(<Host onAnswer={(ok) => answers.push(ok)} />);
  await user.click(screen.getByRole("button", { name: "Ask" }));
  const box = await screen.findByRole("dialog", { name: "Remove it?" });
  expect(box).toHaveTextContent("This cannot be undone.");
  await user.click(screen.getByRole("button", { name: "Confirm" }));
  expect(answers).toEqual([true]);
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("Cancel and Escape answer no, and the box closes", async () => {
  const answers: boolean[] = [];
  const { user } = renderUi(<Host onAnswer={(ok) => answers.push(ok)} />);
  await user.click(screen.getByRole("button", { name: "Ask" }));
  await user.click(await screen.findByRole("button", { name: "Cancel" }));
  await user.click(screen.getByRole("button", { name: "Ask" }));
  await screen.findByRole("dialog");
  await user.keyboard("{Escape}");
  expect(answers).toEqual([false, false]);
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("a risky question makes Confirm the red button", async () => {
  const { user } = renderUi(<Host onAnswer={() => undefined} danger />);
  await user.click(screen.getByRole("button", { name: "Ask" }));
  expect(await screen.findByRole("button", { name: "Confirm" })).toHaveClass("av-button--danger");
});
