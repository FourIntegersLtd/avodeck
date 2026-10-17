// Screen 08, the copies the pipeline set aside: each with its original, confirmed or kept by the
// expert. Every change sends the version it loaded (If-Match). Runs under both pack sets.
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { apiError, http, HttpResponse, json, server, signedIn } from "../../test/api";
import { fixturePack, launchPack } from "../../test/fixtures";
import { axeViolations, renderAt } from "../../test/render";
import { CASE_ID, duplicate, pageId, THUMB, theCase, VIEWER_ACTIONS } from "./testData";

const PATH = `/cases/${CASE_ID}/bundle/copies`;

function mock(pack: Schemas["CasePack"], list: Schemas["DuplicatePage"][], item = theCase()) {
  signedIn({ pack });
  server.use(
    http.get(`/v1/cases/${CASE_ID}`, () => json(item)),
    http.get(`/v1/cases/${CASE_ID}/duplicates`, () => json({ duplicates: list })),
    http.get(`/v1/cases/${CASE_ID}/pages/:pageNo/thumbnail`, () => new HttpResponse(THUMB, { headers: { "Content-Type": "image/jpeg" } })),
  );
}

const row = (page: number) => screen.getByText(`p.${page}`, { exact: false, selector: "h2, h3" }).closest("li") as HTMLElement;

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("Copies under the $name", ({ pack }) => {
  test("lists each copy beside its original, with both pages' pictures", async () => {
    mock(pack, [duplicate(), duplicate({ page_id: pageId(21), page_no: 21, copy_of_page_no: 4, status: "confirmed", version: 2 })]);
    const { container } = renderAt(PATH);
    expect(await screen.findByRole("heading", { level: 1, name: "Copies set aside" })).toBeVisible();
    expect(screen.getByText("p.20 is a copy of p.3")).toBeVisible();
    await waitFor(() => expect(screen.getByRole("img", { name: "Page 20" })).toHaveAttribute("src", expect.stringMatching(/^data:image\/jpeg;base64,/)));
    expect(screen.getByRole("img", { name: "Page 3" })).toBeInTheDocument();
    expect(within(row(20)).getByText("Please check")).toBeVisible();
    expect(within(row(21)).getByText("Set aside")).toBeVisible();
    expect(screen.getByRole("link", { name: "Your bundle" })).toHaveAttribute("href", `/cases/${CASE_ID}/bundle`);
    expect(await axeViolations(container)).toEqual([]);
  });

  test("confirming a copy sends the version it loaded", async () => {
    mock(pack, [duplicate()]);
    let sent: { body: unknown; ifMatch: string | null } | null = null;
    server.use(
      http.patch(`/v1/cases/${CASE_ID}/pages/${pageId(20)}/duplicate`, async ({ request }) => {
        sent = { body: await request.json(), ifMatch: request.headers.get("If-Match") };
        return json(duplicate({ status: "confirmed", version: 2 }));
      }),
    );
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Yes, it's a copy" }));
    expect(await within(row(20)).findByText("Set aside")).toBeVisible();
    expect(sent).toEqual({ body: { action: "confirm" }, ifMatch: '"1"' });
  });

  test("keeping a page puts it back in the bundle", async () => {
    mock(pack, [duplicate({ status: "confirmed", version: 3 })]);
    let sent: unknown = null;
    server.use(
      http.patch(`/v1/cases/${CASE_ID}/pages/${pageId(20)}/duplicate`, async ({ request }) => {
        sent = await request.json();
        return json(duplicate({ status: "kept", version: 4 }));
      }),
    );
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Keep this page" }));
    expect(await within(row(20)).findByText("Kept in the bundle")).toBeVisible();
    expect(sent).toEqual({ action: "undo" });
    expect(within(row(20)).queryByRole("button")).toBeNull();
  });

  test("if someone else changed the page first, it says so and shows the latest", async () => {
    mock(pack, [duplicate()]);
    server.use(http.patch(`/v1/cases/${CASE_ID}/pages/${pageId(20)}/duplicate`, () => apiError(412, "version_mismatch")));
    const { user } = renderAt(PATH);
    const button = await screen.findByRole("button", { name: "Yes, it's a copy" });
    server.use(http.get(`/v1/cases/${CASE_ID}/duplicates`, () => json({ duplicates: [duplicate({ status: "kept", version: 2 })] })));
    await user.click(button);
    expect(await screen.findByText("Someone else changed this page. Here is the latest. Please choose again if you need to.")).toBeVisible();
    expect(await within(row(20)).findByText("Kept in the bundle")).toBeVisible();
  });

  test("someone who cannot review copies sees them without the choices", async () => {
    mock(pack, [duplicate()], theCase({ allowed_actions: VIEWER_ACTIONS }));
    renderAt(PATH);
    await screen.findByText("p.20 is a copy of p.3");
    expect(screen.queryByRole("button", { name: "Yes, it's a copy" })).toBeNull();
  });

  test("with no copies it says so", async () => {
    mock(pack, []);
    renderAt(PATH);
    expect(await screen.findByText("No copies were found.")).toBeVisible();
  });
});
