// S11 on a phone: the axis and lanes scroll sideways together inside the frame; the page itself does not.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { http, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { inCase, timeline } from "./testing";

const css = readFileSync(resolve(process.cwd(), "src/capabilities/understand/understand.css"), "utf8");
// The phone rules sit in one block that starts at this marker and runs to the end of the file's next blank line.
const start = css.indexOf("/* S11 phone board */");
const phoneBlock = start < 0 ? "" : css.slice(start, css.indexOf("\n\n", start));
const picAt = css.indexOf("/* S11 phone panel picture */");
const pictureBlock = picAt < 0 ? "" : css.slice(picAt, css.indexOf("\n\n", picAt));

describe("timeline entry panel picture on a phone", () => {
  test("on a phone the picture controls are hidden and the page shows whole at its natural height", () => {
    expect(pictureBlock).toMatch(/@media \(max-width: 720px\)/);
    expect(pictureBlock).toMatch(/\.av-story-panel \.av-pageview__bar\s*\{[^}]*display:\s*none/);
    expect(pictureBlock).toMatch(/\.av-story-panel \.av-pageview__paper\s*\{[^}]*max-height:\s*none/);
    expect(pictureBlock).toMatch(/\.av-story-panel \.av-pageview__paper\s*\{[^}]*overflow:\s*visible/);
  });
});

describe("timeline board on a phone", () => {
  test("one scroller holds the axis and every lane, inside the frame", async () => {
    inCase("launch");
    server.use(
      http.get("/v1/cases/:caseId/timeline", () => json(timeline())),
      http.get("/v1/cases/:caseId/timeline/overview", () => json({ months: [], lanes: [] })),
    );
    renderAt(`/cases/${CASE_A}/timeline`);
    const board = await screen.findByTestId("timeline-board");
    const scroller = within(board).getByTestId("timeline-scroll");
    expect(board.contains(scroller)).toBe(true);
    expect(scroller.querySelector(".av-story-axis")).not.toBeNull();
    expect(scroller.querySelectorAll(".av-story-lane").length).toBeGreaterThan(0);
  });

  test("the phone rules scroll sideways, keep lane names in view and size from one variable", () => {
    expect(phoneBlock).toMatch(/@media \(max-width: 720px\)/);
    expect(phoneBlock).toMatch(/\.av-story-scroll\s*\{[^}]*overflow-x:\s*auto/);
    expect(phoneBlock).toMatch(/-webkit-overflow-scrolling:\s*touch/);
    expect(phoneBlock).toMatch(/min-width:\s*640px/);
    expect(phoneBlock).toMatch(/\.av-story-lane__head\s*\{[^}]*position:\s*sticky;[^}]*left:\s*0/);
    expect(css).toMatch(/--av-lane-head/);
    expect(css).toMatch(/\.av-story-density\s*\{[^}]*calc\(var\(--av-lane-head/);
    expect(css).not.toMatch(/left:\s*(212|152)px/);
    expect(css).toMatch(/max-height:\s*70dvh/);
    expect(css).toMatch(/var\(--safe-bottom, 0px\)/);
    expect(css).toMatch(/\.av-story-board\s*\{[^}]*overflow:\s*clip/);
  });
});
