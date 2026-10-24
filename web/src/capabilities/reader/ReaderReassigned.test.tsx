// Screen 09: after the named expert changed, a page whose suggestions came back says why, once, in plain
// words. A page that was never read says suggestions appear once it is marked as read.
import { screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { forgetPages } from "./pageImageCache";
import { launchPack } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { CASE_ID } from "./testData";
import { mockReader } from "./testing";
import "./index";

beforeEach(() => {
  forgetPages();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:page"), revokeObjectURL: vi.fn() }));
});

const PATH = `/cases/${CASE_ID}/read/118`;

test("says the case has a new named expert when suggestions are back because of it", async () => {
  mockReader(launchPack, { alreadyRead: true, reassigned: true });
  renderAt(PATH);
  expect(await screen.findByText(/This case has a new named expert\. Every suggestion goes back to be checked again by you\. Nothing has been lost\./)).toBeInTheDocument();
});

test("says nothing about a new expert otherwise", async () => {
  mockReader(launchPack, { alreadyRead: true });
  renderAt(PATH);
  await screen.findByText(/What we found on this page/);
  expect(screen.queryByText(/new named expert/)).toBeNull();
});

test("an unread page says suggestions appear once it is marked as read", async () => {
  mockReader(launchPack, { reassigned: true });
  renderAt(PATH);
  expect(await screen.findByText("Suggestions appear once you mark this page as read.")).toBeInTheDocument();
  expect(screen.queryByText(/new named expert/)).toBeNull();
});
