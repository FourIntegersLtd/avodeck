// Screen 10 as the design canvas shows it: dictation, the "or choose one" actions and the progress pill.
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Schemas } from "../../foundation/platform-client";
import { fixturePack, launchPack } from "../../test/fixtures";
import { http, json, server } from "../../test/api";
import { axeViolations, renderAt } from "../../test/render";
import { CASE_ID, pageId } from "./testData";
import { mockReader } from "./testing";
import "./index";

const PATH = `/cases/${CASE_ID}/read/couldnt-read`;
const DOC = "b7777777-7777-4777-8777-777777777777";

describe.each([
  { name: "launch packs", pack: launchPack },
  { name: "fixture packs", pack: fixturePack },
])("Pages we couldn't read, as designed, under the $name", ({ pack }: { pack: Schemas["CasePack"] }) => {
  test("the box can be dictated into, the progress is a bar, and axe finds nothing", async () => {
    mockReader(pack);
    const { container } = renderAt(PATH);
    expect(await screen.findByRole("button", { name: "Dictate" })).toBeVisible();
    expect(screen.getByText("Your words only")).toBeVisible();
    expect(screen.getByRole("progressbar", { name: "0 of 3 done" })).toHaveAttribute("aria-valuenow", "0");
    expect(await axeViolations(container)).toEqual([]);
  });

  test("Nothing relevant on this page marks it and moves on", async () => {
    const api = mockReader(pack);
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "Nothing relevant on this page" }));
    await waitFor(() => expect(api.patches).toEqual([{ page: 612, body: { relevance: "nothing" } }]));
    expect(await screen.findByRole("img", { name: "Page 640" })).toBeVisible();
    expect(await screen.findByText("1 of 3 done")).toBeVisible();
  });

  test("It's a copy of another page asks which page, then sets this one aside", async () => {
    const api = mockReader(pack);
    const { user } = renderAt(PATH);
    await user.click(await screen.findByRole("button", { name: "It's a copy of another page" }));
    await user.type(screen.getByLabelText("Which page is it a copy of?"), "611");
    await user.click(screen.getByRole("button", { name: "Set this page aside" }));
    await waitFor(() => expect(api.copies).toEqual([{ pageId: pageId(612), body: { copy_of_page_no: 611 } }]));
    expect(await screen.findByRole("img", { name: "Page 640" })).toBeVisible();
  });

  test("I have a clearer copy uploads it as later records and replaces this page with it", async () => {
    const api = mockReader(pack);
    const calls: string[] = [];
    server.use(
      http.post("/v1/cases/:caseId/uploads", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        calls.push(`start:${String(body.kind)}:${String(body.append)}`);
        return json({ upload_id: "a6666666-6666-4666-8666-666666666666", chunk_size: 8_388_608, chunk_count: 1 }, 201);
      }),
      http.put("/v1/uploads/:id/chunks/:n", () => {
        calls.push("chunk");
        return new Response(null, { status: 204 });
      }),
      http.post("/v1/uploads/:id/complete", () => json({ document_id: DOC })),
    );
    const { user } = renderAt(PATH);
    await user.upload(await screen.findByLabelText("I have a clearer copy: add it"), new File([new Uint8Array(500)], "clear.pdf", { type: "application/pdf" }));
    await waitFor(() => expect(api.replaces).toEqual([{ pageId: pageId(612), body: { document_id: DOC } }]));
    expect(calls).toEqual(["start:records:true", "chunk"]);
    expect(api.appends).toEqual([{ document_ids: [DOC] }]);
    expect(await screen.findByRole("img", { name: "Page 640" })).toBeVisible();
  });
});
