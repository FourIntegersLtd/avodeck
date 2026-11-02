import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { axeViolations } from "../../test/render";
import { DropZone } from "./DropZone";

function zone(onFiles = vi.fn(), disabled = false) {
  const view = render(
    <DropZone title="Records" hint="Drop them here" disabled={disabled} onFiles={onFiles}>
      <span data-testid="inner">6 files</span>
    </DropZone>,
  );
  return { ...view, root: view.container.firstElementChild as HTMLElement, inner: screen.getByTestId("inner") };
}

test("the outline stays on while the drag moves over the words inside the card", () => {
  const { root, inner } = zone();
  fireEvent.dragEnter(root);
  fireEvent.dragOver(root);
  expect(root).toHaveClass("av-dropzone--over");
  // Moving onto a child: the child is entered first, then the card is left.
  fireEvent.dragEnter(inner);
  fireEvent.dragLeave(root);
  expect(root).toHaveClass("av-dropzone--over");
  // Leaving the child and the card for good turns it off.
  fireEvent.dragLeave(inner);
  expect(root).not.toHaveClass("av-dropzone--over");
});

test("a drop turns the outline off and hands over the files", () => {
  const onFiles = vi.fn();
  const { root } = zone(onFiles);
  const file = new File(["x"], "a.pdf", { type: "application/pdf" });
  fireEvent.dragEnter(root);
  fireEvent.drop(root, { dataTransfer: { files: [file] } });
  expect(root).not.toHaveClass("av-dropzone--over");
  expect(onFiles).toHaveBeenCalledWith([file]);
});

test("a card that is switched off never lights up or takes files", () => {
  const onFiles = vi.fn();
  const { root } = zone(onFiles, true);
  fireEvent.dragEnter(root);
  fireEvent.dragOver(root);
  expect(root).not.toHaveClass("av-dropzone--over");
  fireEvent.drop(root, { dataTransfer: { files: [new File(["x"], "a.pdf")] } });
  expect(onFiles).not.toHaveBeenCalled();
});

test("passes axe", async () => {
  const { container } = zone();
  expect(await axeViolations(container)).toEqual([]);
});
