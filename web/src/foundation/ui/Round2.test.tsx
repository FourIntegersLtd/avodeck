// Design-system additions asked for by the slice owners (round 2 of the design match).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { renderUi } from "../../test/render";
import { BackLink, Button, CaseHeader, DictationBar, DropZone, IconTile, LinkButton, QChip, StackedBar, ToggleSwitch } from ".";

const read = (f: string) => readFileSync(join(__dirname, f), "utf8");
const rule = (css: string, selector: string) => css.match(new RegExp(`${selector.replace(/[.[\]]/g, "\\$&")}\\s*\\{([^}]*)\\}`))?.[1] ?? "";

test("a question chip is drawn dark, as on the boards", () => {
  expect(rule(read("styles.css"), ".av-qchip")).toMatch(/background:\s*var\(--ink\)/);
  renderUi(<QChip n={3} />);
});

test("pages run full width to the bar's gutter, set once in the tokens", () => {
  expect(read("tokens.css")).toMatch(/--page-width-wide:\s*none/);
  expect(read("tokens.css")).toMatch(/--gutter:\s*\d+px/);
});

test("buttons and link buttons can point back", () => {
  renderUi(
    <>
      <Button arrow="back">{"Back"}</Button>
      <LinkButton to="/x" arrow="back" variant="secondary">
        {"Go back"}
      </LinkButton>
      <Button arrow>{"On"}</Button>
    </>,
  );
  const [back, link, on] = [screen.getByRole("button", { name: "Back" }), screen.getByRole("link", { name: "Go back" }), screen.getByRole("button", { name: "On" })];
  expect(back).toHaveClass("av-button--arrow", "av-button--arrow-back");
  expect(back.firstElementChild?.tagName).toBe("svg");
  expect(link.firstElementChild?.tagName).toBe("svg");
  expect(on).not.toHaveClass("av-button--arrow-back");
  expect(on.lastElementChild?.tagName).toBe("svg");
  renderUi(<BackLink to="/y">{"Return"}</BackLink>);
  expect(screen.getByRole("link", { name: "Return" }).firstElementChild?.tagName).toBe("svg");
});

test("a switch can put its label on the left, switch at the right", () => {
  renderUi(<ToggleSwitch label="Send me emails" checked onChange={() => undefined} labelFirst />);
  const sw = screen.getByRole("switch", { name: "Send me emails" });
  expect(sw).toHaveClass("av-switch--label-first");
  expect(sw.firstElementChild?.tagName).toBe("SPAN");
  expect(rule(read("styles.css"), ".av-switch--label-first")).toMatch(/justify-content:\s*space-between/);
});

test("an icon tile can be grey", () => {
  const { container } = renderUi(<IconTile tone="grey">{"i"}</IconTile>);
  expect(container.querySelector(".av-icon-tile--grey")).not.toBeNull();
  expect(rule(read("styles.css"), ".av-icon-tile--grey")).toMatch(/var\(--surface-soft\)/);
});

test("a stacked bar can stand upright", () => {
  const { container } = renderUi(
    <StackedBar vertical label="Pages by source" segments={[{ key: "a", value: 3, color: "#111", label: "A" }, { key: "b", value: 1, color: "#999", label: "B" }]} />,
  );
  expect(container.querySelector(".av-stacked--vertical")).not.toBeNull();
  const rects = container.querySelectorAll(".av-stacked__bar rect");
  expect(rects).toHaveLength(2);
  expect(rects[0]).toHaveAttribute("height", "75");
  expect(rects[1]).toHaveAttribute("y", "75");
  expect(screen.getByRole("img", { name: "Pages by source" })).toBeInTheDocument();
});

test("a drop zone can show that its files were added", () => {
  const { container } = renderUi(
    <DropZone title="Medical records" added addedLabel="Added" onFiles={() => undefined}>
      <span>{"6 files"}</span>
    </DropZone>,
  );
  expect(container.querySelector(".av-dropzone--added")).not.toBeNull();
  expect(screen.getByText("Added")).toBeInTheDocument();
  expect(screen.getByText("6 files")).toBeInTheDocument();
  expect(container.querySelector("input[type=file]")).not.toBeNull();
});

test("a dictation bar can carry a short note beside it", () => {
  renderUi(<DictationBar caseId="c" context="x" label="Words" value="" onChange={() => undefined} note="Saved as you go" />);
  expect(screen.getByText("Saved as you go")).toHaveClass("av-dictation__note");
});

test("the current step shows active on the case header", () => {
  renderUi(<CaseHeader steps={[{ key: "prepare" }, { key: "read" }]} current="prepare" />);
  expect(screen.getByRole("listitem", { current: "step" })).toHaveClass("av-step--current");
});
