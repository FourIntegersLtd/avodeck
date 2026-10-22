// S18 as drawn on the board: a tick beside the title, the document and the drawn signature side by side, the
// declaration in the pack's name for it, the note saying who it goes to, and one main action with an arrow.
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { casePack, launchWords } from "../setup/testing";
import { declarations, finalCheck, partyWord, serve, type PackRun } from "./testing";
import { REPORT_MESSAGES as M } from "./words";

const url = `/cases/${CASE_A}/sign`;
const declarationName = (run: PackRun) => String((casePack(run).vocabulary as Record<string, unknown>).declaration_name);

describe.each<PackRun>(["launch", "fixture"])("under the %s packs", (run) => {
  test("the declaration card carries the pack's name for it, and the tick box uses it too", async () => {
    serve(run, { check: finalCheck() });
    const { container } = renderAt(url);
    expect(await screen.findByRole("heading", { name: declarationName(run).charAt(0).toUpperCase() + declarationName(run).slice(1) })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: `I have read the whole report and I confirm the ${declarationName(run).toLowerCase()}.` })).toBeInTheDocument();
    for (const d of declarations(run)) for (const part of d.text.split("\n\n")) expect(screen.getByText(part)).toBeInTheDocument();
    if (run === "fixture") expect(document.body.textContent).not.toMatch(launchWords());
    expect(await axeViolations(container)).toEqual([]);
  });

  test("a note says who it goes to, in the pack's word, and the main action names them too", async () => {
    serve(run, { check: finalCheck() });
    renderAt(url);
    const party = partyWord(run);
    expect(await screen.findByText(new RegExp(`^Sending to the ${party} by email today`))).toBeInTheDocument();
    const button = screen.getByRole("button", { name: `Sign and send to the ${party}` });
    expect(button.querySelector("svg")).not.toBeNull();
    expect(button).toBeDisabled();
  });
});

test("the title has a tick tile, and the document and signature are cards side by side", async () => {
  serve("launch", { check: finalCheck() });
  renderAt(url);
  const title = await screen.findByRole("heading", { level: 1, name: M["report.sign.title_ready"]! });
  expect(title.parentElement?.parentElement?.querySelector(".rp-hero__badge")).not.toBeNull();
  const doc = screen.getByRole("region", { name: "Draft 1" });
  expect(within(doc).getByText(/^31 pages · \d+ appendices$/)).toBeInTheDocument();
  const signature = screen.getByRole("region", { name: M["report.sign.signature"]! });
  expect(within(signature).getByRole("img", { name: M["report.sign.signature_alt"]! })).toHaveAttribute("src", expect.stringContaining("/v1/me/signature/image"));
  expect(await within(signature).findByText("Jane Morgan, Doctor · Body 123")).toBeInTheDocument();
  expect(doc.parentElement).toBe(signature.parentElement);
});

test("the downloads sit in the same row as the main action", async () => {
  serve("launch", { check: finalCheck() });
  renderAt(url);
  const word = await screen.findByRole("link", { name: M["report.sign.download_word"]! });
  const sign = screen.getByRole("button", { name: /^Sign and send to the / });
  expect(word.parentElement?.parentElement).toBe(sign.parentElement);
});
