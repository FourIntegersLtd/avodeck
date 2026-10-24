// The calm design system (docs/design: DesignSystemCalm board): buttons, cards, the step bar and the new parts.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, test, vi } from "vitest";
import { axeViolations, renderUi } from "../../test/render";
import { Button, CaseHeader, ListRow, Menu, MenuItem, MenuSection, Notice, SegmentedControl, StatusMark, StepperCapsule } from ".";

const styles = readFileSync(join(__dirname, "styles.css"), "utf8");
const tokens = readFileSync(join(__dirname, "tokens.css"), "utf8");
const rule = (selector: string) => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const found = [...styles.matchAll(new RegExp(`(?:^|\\n|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`, "g"))];
  return found.length ? found[found.length - 1]![1]! : "";
};

test("tokens carry the calm well, hairline, shadows and radii", () => {
  expect(tokens).toMatch(/--well:\s*#F1F2F1/i);
  expect(tokens).toMatch(/--hairline:\s*#E9ECEA/i);
  expect(tokens).toMatch(/--shadow-card:/);
  expect(tokens).toMatch(/--shadow-paper:/);
  expect(tokens).toMatch(/--radius-card:\s*16px/);
});

test("buttons: primary 56px green, secondary a soft grey fill without a border, danger soft red, all 52px or more", () => {
  expect(rule(".av-button")).toMatch(/min-height:\s*56px/);
  expect(rule(".av-button")).toMatch(/border-radius:\s*12px/);
  expect(rule(".av-button--secondary")).toMatch(/background:\s*var\(--well\)/);
  expect(rule(".av-button--secondary")).toMatch(/border-color:\s*transparent/);
  expect(rule(".av-button--outline")).toMatch(/border:\s*1\.5px solid var\(--line\)/);
  expect(rule(".av-button--danger")).toMatch(/background:\s*var\(--red-tint\)/);
  expect(rule(".av-button--quiet")).toMatch(/background:\s*transparent/);
  expect(rule(".av-button--secondary")).toMatch(/min-height:\s*var\(--button-min-height\)/);
});

test("the button variants render with their class", () => {
  renderUi(
    <>
      <Button variant="outline">{"Done"}</Button>
      <Button variant="link">{"Show all"}</Button>
    </>,
  );
  expect(screen.getByRole("button", { name: "Done" })).toHaveClass("av-button--outline");
  expect(screen.getByRole("button", { name: "Show all" })).toHaveClass("av-button--link");
});

test("a card has radius 16, a soft shadow and no border; paper has radius 3", () => {
  expect(rule(".av-card")).toMatch(/border-radius:\s*var\(--radius-card\)/);
  expect(rule(".av-card")).toMatch(/box-shadow:\s*var\(--shadow-card\)/);
  expect(rule(".av-card")).not.toMatch(/border:\s*1px/);
  expect(rule(".av-paper")).toMatch(/border-radius:\s*3px/);
  expect(rule(".av-paper")).toMatch(/box-shadow:\s*var\(--shadow-paper\)/);
});

test("the step bar is a grey track with the current step a raised white segment and a tick on done steps", () => {
  const { container } = renderUi(
    <MemoryRouter>
      <CaseHeader bar caseId="c1" steps={[{ key: "prepare" }, { key: "read" }, { key: "write" }]} current="read" progress={{ read: { done: 3, total: 4, percent: 75 } }} title="Mrs A" />
    </MemoryRouter>,
  );
  expect(rule(".av-steps")).toMatch(/background:\s*var\(--well\)/);
  expect(rule(".av-step--current .av-step__pill")).toMatch(/box-shadow:/);
  expect(rule(".av-step--current .av-step__pill")).toMatch(/background:\s*var\(--surface\)/);
  expect(container.querySelector(".av-step--done svg.av-step__tick")).not.toBeNull();
  expect(container.querySelector(".av-step--current")).toHaveAttribute("aria-current", "step");
  expect(container.querySelector(".av-step--current .av-step__pct")).toHaveTextContent("3 of 4");
  expect(rule(".av-step__pct")).toMatch(/font-family:\s*var\(--font-mono\)/);
});

test("SegmentedControl: buttons with aria-pressed, one chosen", async () => {
  const onChange = vi.fn();
  const { user } = renderUi(<SegmentedControl label="Dates" value="b" onChange={onChange} options={[{ value: "a", label: "Whole record" }, { value: "b", label: "Last 2 years" }]} />);
  expect(screen.getByRole("group", { name: "Dates" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Last 2 years" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "Whole record" })).toHaveAttribute("aria-pressed", "false");
  await user.click(screen.getByRole("button", { name: "Whole record" }));
  expect(onChange).toHaveBeenCalledWith("a");
});

test("StepperCapsule wraps previous, value and next", () => {
  const { container } = renderUi(
    <StepperCapsule label="Page">
      <button type="button" aria-label="Previous page">{"<"}</button>
      <span>{"80"}</span>
    </StepperCapsule>,
  );
  expect(screen.getByRole("group", { name: "Page" })).toBeInTheDocument();
  expect(container.querySelector(".av-capsule")).not.toBeNull();
});

test("Menu opens from its trigger, ticks the pressed row, shows shortcuts, closes on Escape and returns focus", async () => {
  const onPick = vi.fn();
  const { user } = renderUi(
    <Menu label="Show" trigger={<>{"Show: Everything"}</>}>
      <MenuSection label="Show">
        <MenuItem pressed onClick={onPick}>{"Everything"}</MenuItem>
        <MenuItem shortcut="?">{"Keyboard shortcuts"}</MenuItem>
      </MenuSection>
    </Menu>,
  );
  const trigger = screen.getByRole("button", { name: /Show: Everything/ });
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  await user.click(trigger);
  expect(trigger).toHaveAttribute("aria-expanded", "true");
  const row = screen.getByRole("button", { name: /Everything/, pressed: true });
  expect(row.querySelector(".av-menu__tick svg")).not.toBeNull();
  expect(screen.getByText("?")).toBeInTheDocument();
  await user.click(row);
  expect(onPick).toHaveBeenCalled();
  await user.keyboard("{Escape}");
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  expect(trigger).toHaveFocus();
  await user.click(trigger);
  await user.click(document.body);
  expect(trigger).toHaveAttribute("aria-expanded", "false");
});

test("ListRow: a button row with a mark, title, quote and an expanded slot", async () => {
  const { container } = renderUi(
    <ul>
      <ListRow mark={<StatusMark status="please_check" />} title="Consultation · 4.2.98" quote="Headache, tension type" expanded={false} onToggle={() => undefined}>
        <button type="button">{"Correct"}</button>
      </ListRow>
    </ul>,
  );
  const row = screen.getByRole("button", { name: /Consultation/ });
  expect(row).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("button", { name: "Correct" })).toBeNull();
  expect(container.querySelector(".av-list-row__quote")).toHaveTextContent("Headache, tension type");
  expect(rule(".av-list-row__quote")).toMatch(/Georgia/);
});

test("ListRow shows its expanded slot when expanded", () => {
  renderUi(
    <ul>
      <ListRow title="x" expanded onToggle={() => undefined}>
        <button type="button">{"Correct"}</button>
      </ListRow>
    </ul>,
  );
  expect(screen.getByRole("button", { name: "Correct" })).toBeInTheDocument();
});

test("StatusMark has an accessible name for each status", () => {
  renderUi(
    <>
      <StatusMark status="checked" />
      <StatusMark status="please_check" />
      <StatusMark status="not_right" />
      <StatusMark status="look" />
    </>,
  );
  for (const name of ["Checked by you", "Please check", "Not right", "Thing to look at"]) expect(screen.getByRole("img", { name })).toBeInTheDocument();
  expect(rule(".av-mark--please_check")).toMatch(/dashed/);
});

test("Notice has borderless amber and mint tones", () => {
  renderUi(
    <>
      <Notice tone="amber">{"Look at this"}</Notice>
      <Notice tone="mint">{"In the period"}</Notice>
    </>,
  );
  expect(rule(".av-notice--amber")).toMatch(/background:\s*var\(--amber-tint\)/);
  expect(rule(".av-notice--amber")).not.toMatch(/border:/);
  expect(rule(".av-notice--mint")).toMatch(/background:\s*var\(--green-tint\)/);
  expect(screen.getByText("Look at this")).toHaveClass("av-notice--amber");
});

test("the focus ring is amber and 3px", () => {
  expect(rule(":focus-visible")).toMatch(/outline:\s*3px solid var\(--focus\)/);
});

test("the new parts have no accessibility violations", async () => {
  const { container } = renderUi(
    <div>
      <SegmentedControl label="Dates" value="a" onChange={() => undefined} options={[{ value: "a", label: "A" }, { value: "b", label: "B" }]} />
      <ul>
        <ListRow mark={<StatusMark status="checked" />} title="T" quote="Q" expanded={false} onToggle={() => undefined} />
      </ul>
    </div>,
  );
  expect(await axeViolations(container)).toEqual([]);
});
