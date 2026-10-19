// Design-system components (web/AGENTS.md "Design system", docs/design/screens/Main.dc.html).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createEvent, fireEvent, screen, within } from "@testing-library/react";
import { useState } from "react";
import { expect, test, vi } from "vitest";
import { renderUi } from "../../test/render";
import { BackLink, Button, Callout, CaseHeader, ChoicePills, DictationBar, DropZone, IconTile, LinkButton, Loading, PageRef, ProgressBar, ProgressRing, QChip, Segmented, Spinner, StackedBar, StatusBadge, StatusRow, TextField, TextLink, ToggleSwitch } from ".";

const css = readFileSync(join(__dirname, "tokens.css"), "utf8");

test("body text is at least 17px and buttons at least 52px tall (U2)", () => {
  const body = css.match(/--text-body:\s*(\d+)px/);
  const button = css.match(/--button-min-height:\s*(\d+)px/);
  expect(Number(body?.[1])).toBeGreaterThanOrEqual(17);
  expect(Number(button?.[1])).toBeGreaterThanOrEqual(52);
  expect(css).toMatch(/--green:\s*#1E6B4E/i);
});

test("a button is type=button unless it submits", () => {
  renderUi(
    <>
      <Button>{"x"}</Button>
      <Button type="submit">{"y"}</Button>
    </>,
  );
  const [plain, submit] = screen.getAllByRole("button");
  expect(plain).toHaveAttribute("type", "button");
  expect(submit).toHaveAttribute("type", "submit");
});

test("a text field is labelled and shows its error to assistive technology", () => {
  renderUi(
    <TextField label="Email address" name="email" error="That didn't work." />,
  );
  const input = screen.getByLabelText("Email address");
  expect(input).toHaveAttribute("aria-invalid", "true");
  expect(input).toHaveAccessibleDescription("That didn't work.");
});

test("the case header marks done steps with a tick and the current step for screen readers", () => {
  renderUi(
    <CaseHeader
      steps={[{ key: "prepare" }, { key: "read" }, { key: "write" }]}
      current="read"
    />,
  );
  const [prepare, read, write] = screen.getAllByRole("listitem");
  expect(prepare).toHaveTextContent(/^Prepare$/);
  expect(prepare!.querySelector("svg")).not.toBeNull();
  expect(read).toHaveAttribute("aria-current", "step");
  expect(read).toHaveTextContent("2Read");
  expect(write).not.toHaveAttribute("aria-current");
  expect(screen.getByRole("navigation")).toHaveAccessibleName(
    "Steps for this case",
  );
});

test("status badges use the glossary words, never colour alone", () => {
  renderUi(
    <>
      <StatusBadge status="checked" />
      <StatusBadge status="please_check" />
      <StatusBadge status="not_right" />
      <StatusBadge status="look" />
    </>,
  );
  for (const words of [
    "Checked by you",
    "Please check",
    "Not right",
    "Thing to look at",
  ]) {
    expect(screen.getByText(words)).toBeInTheDocument();
  }
});

test("a checkbox's whole label is a hit area at least 52px tall (U2)", () => {
  const styles = readFileSync(join(__dirname, "styles.css"), "utf8");
  const rule = styles.match(/\.av-checkbox\s*\{([^}]*)\}/)?.[1] ?? "";
  const height = rule.match(/min-height:\s*([^;]+);/)?.[1]?.trim() ?? "";
  const px =
    height === "var(--button-min-height)"
      ? Number(css.match(/--button-min-height:\s*(\d+)px/)?.[1])
      : Number(height.match(/^(\d+)px$/)?.[1]);
  expect(px).toBeGreaterThanOrEqual(52);
});

const rule = (selector: string) => {
  const styles = readFileSync(join(__dirname, "styles.css"), "utf8");
  const escaped = selector.replace(/[.[\]()]/g, "\\$&");
  return styles.match(new RegExp(`(?:^|\\n|,\\s*)${escaped}\\s*(?:,[^{]*)?\\{([^}]*)\\}`))?.[1] ?? "";
};

test("tokens follow the canvas: 17px body, 1.5 line height, card radius 18, blue tints, page widths", () => {
  expect(css).toMatch(/--text-body:\s*17px/);
  expect(css).toMatch(/--line-height:\s*1\.5\s*;/);
  expect(css).toMatch(/--radius-large:\s*18px/);
  expect(css).toMatch(/--blue-tint:/);
  expect(css).toMatch(/--page-width:/);
  expect(css).toMatch(/--page-width-narrow:/);
});

test("no text in the design system is smaller than 17px (U2)", () => {
  const styles = readFileSync(join(__dirname, "styles.css"), "utf8");
  const small = [...styles.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)].filter((m) => Number(m[1]) < 17).map((m) => m[0]);
  expect(small).toEqual([]);
});

test("every tap area in the design system is at least 52px, even where the shape looks smaller", () => {
  for (const selector of [".av-pill", ".av-segmented__option", ".av-switch", ".av-text-link", ".av-back-link", ".av-shell__bell", ".av-shell__avatar", ".av-step"]) {
    expect(rule(selector), selector).toMatch(/min-height:\s*var\(--button-min-height\)/);
  }
});

function Pills({ tone }: { tone?: "amber" }) {
  const [value, setValue] = useState("a");
  return (
    <ChoicePills
      label="Who is this case for?"
      name="side"
      tone={tone}
      value={value}
      onChange={setValue}
      options={[
        { value: "a", label: "First" },
        { value: "b", label: "Second" },
      ]}
    />
  );
}

test("choice pills are one radio group with a tick on the selected pill", async () => {
  const { user, container } = renderUi(<Pills />);
  const group = screen.getByRole("radiogroup", { name: "Who is this case for?" });
  const [first, second] = within(group).getAllByRole("radio");
  expect(first).toBeChecked();
  expect(container.querySelectorAll(".av-pill--selected svg")).toHaveLength(1);
  await user.click(second!);
  expect(second).toBeChecked();
  expect(container.querySelector(".av-pill--selected")).toHaveTextContent("Second");
  expect(container.querySelectorAll(".av-pill svg")).toHaveLength(1);
  await user.keyboard("{ArrowLeft}");
  expect(first).toBeChecked();
});

test("choice pills have an amber variant", () => {
  const { container } = renderUi(<Pills tone="amber" />);
  expect(container.querySelector(".av-pill-group--amber")).not.toBeNull();
});

test("a segmented control is a radio group too", async () => {
  const onChange = vi.fn();
  const { user } = renderUi(
    <Segmented
      label="Show"
      value="list"
      onChange={onChange}
      options={[
        { value: "list", label: "List" },
        { value: "lanes", label: "Lanes" },
      ]}
    />,
  );
  const group = screen.getByRole("radiogroup", { name: "Show" });
  expect(within(group).getByRole("radio", { name: "List" })).toBeChecked();
  await user.click(within(group).getByRole("radio", { name: "Lanes" }));
  expect(onChange).toHaveBeenCalledWith("lanes");
});

test("a toggle switch is a switch that says whether it is on", async () => {
  const onChange = vi.fn();
  const { user } = renderUi(<ToggleSwitch label="Show as seen on the day" checked={false} onChange={onChange} />);
  const toggle = screen.getByRole("switch", { name: "Show as seen on the day" });
  expect(toggle).toHaveAttribute("aria-checked", "false");
  await user.click(toggle);
  expect(onChange).toHaveBeenCalledWith(true);
});

test("a drop zone takes dropped files and, from the keyboard, a chosen file", async () => {
  const onFiles = vi.fn();
  const { user, container } = renderUi(<DropZone title="Medical records" hint="Drag files here" accept=".pdf" multiple onFiles={onFiles} />);
  const input = screen.getByLabelText("Choose files") as HTMLInputElement;
  expect(input.type).toBe("file");
  expect(input.multiple).toBe(true);
  const file = new File(["x"], "a.pdf", { type: "application/pdf" });
  await user.upload(input, file);
  expect(onFiles).toHaveBeenLastCalledWith([file]);
  const zone = container.querySelector(".av-dropzone")!;
  const dropped = new File(["y"], "b.pdf", { type: "application/pdf" });
  fireEvent.dragOver(zone);
  expect(zone).toHaveClass("av-dropzone--over");
  const drop = createEvent.drop(zone);
  Object.defineProperty(drop, "dataTransfer", { value: { files: [dropped] } });
  fireEvent(zone, drop);
  expect(onFiles).toHaveBeenLastCalledWith([dropped]);
  expect(zone).not.toHaveClass("av-dropzone--over");
});

test("the ring, the stacked bar and the progress bar are SVG with no inline styles", () => {
  const { container } = renderUi(
    <>
      <ProgressRing value={3} max={5} label="3 of 5 done" />
      <StackedBar
        label="Balance"
        segments={[
          { key: "a", value: 30, color: "#1E6B4E", label: "Paid" },
          { key: "b", value: 10, color: "#B45309", label: "Owed" },
        ]}
      />
      <ProgressBar value={4} max={8} label="Pages read" />
    </>,
  );
  expect(container.querySelector("[style]")).toBeNull();
  const ring = screen.getByRole("img", { name: "3 of 5 done" });
  const arc = ring.querySelector("circle[stroke-dasharray]")!;
  expect(arc.getAttribute("stroke-width")).toBe("9");
  const [filled, rest] = arc.getAttribute("stroke-dasharray")!.split(" ").map(Number);
  expect(filled! / (filled! + rest!)).toBeCloseTo(0.6, 2);
  const widths = [...screen.getByRole("img", { name: "Balance" }).querySelectorAll("rect")].map((r) => Number(r.getAttribute("width")));
  expect(widths[0]! / widths[1]!).toBeCloseTo(3, 1);
  const bar = screen.getByRole("progressbar", { name: "Pages read" });
  expect(bar).toHaveAttribute("aria-valuenow", "4");
  expect(bar).toHaveAttribute("aria-valuemax", "8");
  expect(rule(".av-progress")).toMatch(/height:\s*12px/);
});

test("a spinner is hidden from assistive technology and Loading uses it", () => {
  const { container } = renderUi(
    <>
      <Spinner />
      <Loading />
    </>,
  );
  expect(container.querySelectorAll(".av-spinner")).toHaveLength(2);
  expect(container.querySelector(".av-spinner")).toHaveAttribute("aria-hidden", "true");
  expect(screen.getByRole("status")).toHaveTextContent("Loading…");
});

test("an icon tile is mint or amber", () => {
  const { container } = renderUi(
    <>
      <IconTile>{"i"}</IconTile>
      <IconTile tone="amber">{"j"}</IconTile>
    </>,
  );
  expect(container.querySelector(".av-icon-tile--mint")).not.toBeNull();
  expect(container.querySelector(".av-icon-tile--amber")).not.toBeNull();
});

test("a callout is an amber alert with its title, words and action slots", () => {
  renderUi(
    <Callout title="Outside your expertise" actions={<Button>{"Answer timing only"}</Button>}>
      {"Check this one."}
    </Callout>,
  );
  const alert = screen.getByRole("alert");
  expect(alert).toHaveClass("av-callout");
  expect(within(alert).getByText("Outside your expertise")).toBeInTheDocument();
  expect(within(alert).getByRole("button", { name: "Answer timing only" })).toBeInTheDocument();
});

test("a status row says its state in words, with a link and a count", () => {
  renderUi(
    <ul>
      <StatusRow status="ok" label="Letter read" />
      <StatusRow status="warn" label="Dates to check" count={3} to="/x" linkLabel="Open" />
      <StatusRow status="stop" label="Missing pages" />
    </ul>,
  );
  const rows = screen.getAllByRole("listitem");
  expect(rows[0]).toHaveTextContent("Done");
  expect(rows[1]).toHaveTextContent("Needs a look");
  expect(rows[1]).toHaveTextContent("3");
  expect(within(rows[1]!).getByRole("link", { name: "Open" })).toHaveAttribute("href", "/x");
  expect(rows[2]).toHaveTextContent("Problem");
});

test("a Q chip and a page reference use their short forms", () => {
  renderUi(
    <>
      <QChip n={3} />
      <PageRef page={118} />
    </>,
  );
  expect(screen.getByText("Q3")).toHaveClass("av-qchip");
  expect(screen.getByText("p.118")).toHaveClass("av-mono");
});

test("text links, back links and arrow buttons are links", () => {
  renderUi(
    <>
      <TextLink to="/a">{"Edit"}</TextLink>
      <BackLink to="/today" />
      <LinkButton to="/b" arrow>
        {"Prepare my bundle"}
      </LinkButton>
    </>,
  );
  expect(screen.getByRole("link", { name: "Edit" })).toHaveAttribute("href", "/a");
  expect(screen.getByRole("link", { name: "Back" })).toHaveAttribute("href", "/today");
  expect(screen.getByRole("link", { name: "Prepare my bundle" }).querySelector("svg")).not.toBeNull();
});

test("a button can carry the arrow of a call to action that moves on", () => {
  renderUi(
    <Button arrow onClick={() => undefined}>
      {"Confirm and continue"}
    </Button>,
  );
  const button = screen.getByRole("button", { name: "Confirm and continue" });
  expect(button.querySelector("svg")).not.toBeNull();
  expect(button).toHaveClass("av-button--arrow");
});

test("radio buttons are drawn in the house green, not the browser's blue", () => {
  const styles = readFileSync(join(__dirname, "styles.css"), "utf8");
  expect(styles).toMatch(/input\[type="radio"\][^{]*\{[^}]*accent-color:\s*var\(--green\)/);
});

test("the dictation bar says the words are the expert's own and offers Dictate", () => {
  renderUi(<DictationBar caseId="c" context="oral" label="What you were told" value="" onChange={() => undefined} />);
  expect(screen.getByLabelText("What you were told")).toBeInTheDocument();
  expect(screen.getByText("Your words only")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Dictate" })).toBeInTheDocument();
});
