// S21b as designed: the kind of change as two cards, each paragraph as "your report said / your view now / why it
// changed", another paragraph, the panel of what we checked, the summary card and the footer.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { CASE_A } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { change, P2, serve, V2 } from "./testing";

const url = `/cases/${CASE_A}/correct-report`;
const supplementary = { id: V2, display_name: "Supplementary 1", status: "draft", version_no: 2, locked_at: null, prev_version_id: "v1", sha256: null };
const withDependent = change({ suggested_dependents: [{ paragraph_id: P2, depends: true, reason: "shares a citation" }] });

test("a saved change shows what the report said struck through, the view now and why it changed", async () => {
  serve("launch", { changes: [withDependent] });
  const { container } = renderAt(url);
  const card = await screen.findByRole("article", { name: "Paragraph 614" });
  expect(within(card).getByText("Your report said")).toBeInTheDocument();
  expect(within(card).getByText("Referred in early March.")).toHaveStyle({ textDecoration: "line-through" });
  expect(within(card).getByText("Your view now")).toBeInTheDocument();
  expect(within(card).getByText("Referred by 15 May at the latest.")).toBeInTheDocument();
  expect(within(card).getByText("Why it changed")).toBeInTheDocument();
  expect(within(card).getByText("Having considered their report.")).toBeInTheDocument();
  expect(await axeViolations(container)).toEqual([]);
});

test("the kind of change is a group of two option cards, each with its own hint", async () => {
  serve("launch");
  renderAt(url);
  const group = await screen.findByRole("radiogroup", { name: "What kind of change is it?" });
  expect(within(group).getAllByRole("radio")).toHaveLength(2);
  expect(within(group).getByText("A fact or detail was wrong")).toBeInTheDocument();
});

test("the served report waits behind a closed disclosure, the checked panel and summary sit in the side column", async () => {
  serve("launch", { changes: [change()] });
  renderAt(url);
  const served = (await screen.findByText("Report 1 · locked")).closest("details") as HTMLDetailsElement;
  expect(served.open).toBe(false);
  const side = screen.getByRole("complementary", { name: "What we've checked" }).parentElement as HTMLElement;
  expect(within(side).getByRole("region", { name: "Supplementary report" })).toBeInTheDocument();
});

test("another paragraph opens a fresh form beside the saved change", async () => {
  serve("launch", { changes: [change()] });
  const { user } = renderAt(url);
  expect(screen.queryByLabelText("Which paragraph?")).toBeNull();
  await user.click(await screen.findByRole("button", { name: "+ Another paragraph" }));
  expect(screen.getByLabelText("Which paragraph?")).toBeInTheDocument();
});

test("the checked panel lists the paragraphs that rely on the change, the reason and the declaration", async () => {
  serve("launch", { changes: [withDependent] });
  renderAt(url);
  const panel = await screen.findByRole("complementary", { name: "What we've checked" });
  expect(within(panel).getByText("Every paragraph that relies on 614 is listed: 73.")).toBeInTheDocument();
  expect(within(panel).getByText("The reason is in your own words.")).toBeInTheDocument();
  expect(within(panel).getByText("Your declaration is added again.")).toBeInTheDocument();
});

test("the summary card says who, what it changes and which report it refers to", async () => {
  serve("launch", { changes: [withDependent], supplementary });
  renderAt(url);
  const card = await screen.findByRole("region", { name: "Supplementary report" });
  expect(within(card).getByText("Changes to: paragraphs 614")).toBeInTheDocument();
  expect(within(card).getByText("Refers to your report of 28 November 2026")).toBeInTheDocument();
});

test("the footer offers to read it in full, and a note says to tell the instructing party without delay", async () => {
  serve("launch", { changes: [change()] });
  const { user } = renderAt(url);
  expect(await screen.findByText(/without delay/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Read it in full" }));
  expect(await screen.findByRole("region", { name: "Your supplementary report in full" })).toBeInTheDocument();
});

test("a signed supplementary report says who signed it and when", async () => {
  serve("launch", {
    changes: [withDependent],
    supplementary: { ...supplementary, status: "locked", locked_at: "2027-01-05T10:00:00Z", signed_by: "Dr Sam Example", signed_on: "2027-01-05" },
  });
  renderAt(url);
  expect(await screen.findByText("Signed by Dr Sam Example, 5 January 2027")).toBeInTheDocument();
});
