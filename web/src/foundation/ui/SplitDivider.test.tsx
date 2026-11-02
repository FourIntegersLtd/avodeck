import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import { describe, expect, test, vi } from "vitest";
import { axeViolations } from "../../test/render";
import { SplitDivider } from "./SplitDivider";

function Harness({ onCommit, start = 50 }: { onCommit?: (pct: number) => void; start?: number }) {
  const area = useRef<HTMLDivElement>(null);
  const [pct, setPct] = useState(start);
  return (
    <div
      ref={(el) => {
        (area as { current: HTMLDivElement | null }).current = el;
        if (el) el.getBoundingClientRect = () => ({ width: 1000 }) as DOMRect;
      }}
    >
      <SplitDivider areaRef={area} value={pct} min={20} max={80} label="Resize" onChange={setPct} onCommit={onCommit} />
    </div>
  );
}

const grip = () => screen.getByRole("separator", { name: "Resize" });
const pointer = (type: string, x: number) => new MouseEvent(type, { bubbles: true, clientX: x });

describe("SplitDivider", () => {
  test("arrow keys move it by 2, Shift by 10, Home and End go to the ends, and it says how wide it is", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(grip()).toHaveAttribute("aria-valuetext");
    await user.tab();
    await user.keyboard("{ArrowRight}");
    expect(grip()).toHaveAttribute("aria-valuenow", "52");
    await user.keyboard("{Shift>}{ArrowLeft}{/Shift}");
    expect(grip()).toHaveAttribute("aria-valuenow", "42");
    await user.keyboard("{End}");
    expect(grip()).toHaveAttribute("aria-valuenow", "80");
    await user.keyboard("{Home}");
    expect(grip()).toHaveAttribute("aria-valuenow", "20");
  });

  test("a key press is committed, and a drag is committed once when it ends", () => {
    const onCommit = vi.fn();
    render(<Harness onCommit={onCommit} />);
    fireEvent.keyDown(grip(), { key: "ArrowRight" });
    expect(onCommit).toHaveBeenLastCalledWith(52);
    fireEvent(grip(), pointer("pointerdown", 500));
    document.dispatchEvent(pointer("pointermove", 600));
    expect(grip()).toHaveAttribute("aria-valuenow", "62");
    expect(onCommit).toHaveBeenCalledTimes(1);
    document.dispatchEvent(pointer("pointerup", 600));
    expect(onCommit).toHaveBeenCalledTimes(2);
    expect(onCommit).toHaveBeenLastCalledWith(62);
  });

  test("a cancelled drag (the touch was taken over) ends the drag too", () => {
    const onCommit = vi.fn();
    render(<Harness onCommit={onCommit} />);
    fireEvent(grip(), pointer("pointerdown", 500));
    document.dispatchEvent(pointer("pointermove", 550));
    document.dispatchEvent(pointer("pointercancel", 550));
    expect(onCommit).toHaveBeenLastCalledWith(55);
    document.dispatchEvent(pointer("pointermove", 900));
    expect(grip()).toHaveAttribute("aria-valuenow", "55");
  });

  test("leaving the screen mid-drag removes the listeners", () => {
    const onChange = vi.fn();
    const area = { current: document.createElement("div") };
    area.current.getBoundingClientRect = () => ({ width: 1000 }) as DOMRect;
    const { unmount } = render(<SplitDivider areaRef={area} value={50} min={20} max={80} label="Resize" onChange={onChange} />);
    fireEvent(grip(), pointer("pointerdown", 500));
    unmount();
    document.dispatchEvent(pointer("pointermove", 700));
    expect(onChange).not.toHaveBeenCalled();
  });

  test("passes axe", async () => {
    const { container } = render(<Harness />);
    expect(await axeViolations(container)).toEqual([]);
  });
});
