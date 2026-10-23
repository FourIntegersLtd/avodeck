// S04: when one of several dropped files can't be added, the others are kept and the page says which
// were saved and which weren't, by file name, in plain words.
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { CASE_B, me, TEAM_ORG } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { caseItem, casePack, combination, jurisdiction } from "./testing";

const file = (name: string) => new File([new Uint8Array(10)], name, { type: "application/pdf" });

test("one file too big: the other is saved and both are named", async () => {
  signedIn({ me: me(), pack: casePack("launch") });
  let starts = 0;
  server.use(
    http.get("/v1/packs", () => json([combination("launch")])),
    http.get("/v1/jurisdictions/:code", () => json(jurisdiction("launch"))),
    http.post("/v1/cases", () => HttpResponse.json(caseItem("launch", { id: CASE_B, organisation_id: TEAM_ORG }), { status: 201, headers: { etag: '"1"' } })),
    http.post("/v1/cases/:caseId/uploads", () =>
      ++starts === 1
        ? HttpResponse.json({ error: { code: "file_too_large" } }, { status: 413 })
        : json({ upload_id: "a6666666-6666-4666-8666-666666666666", chunk_size: 8_388_608, chunk_count: 1 }, 201),
    ),
    http.put("/v1/uploads/:id/chunks/:n", () => new HttpResponse(null, { status: 204 })),
    http.post("/v1/uploads/:id/complete", () => json({ document_id: "b7777777-7777-4777-8777-777777777771" })),
    http.get("/v1/cases/:caseId/prefill", () => json({ reading: false, rejected: 0, suggestions: [] })),
    http.get("/v1/cases/:caseId/files", () => json([])),
  );
  const { user } = renderAt("/cases/new");
  await user.type(await screen.findByLabelText("A short name for the case"), "Mrs B");
  const zone = screen.getByText("Medical records").closest(".av-dropzone") as HTMLElement;
  await user.upload(zone.querySelector("input[type=file]") as HTMLInputElement, [file("big.pdf"), file("ok.pdf")]);
  const notice = await screen.findByText(/big\.pdf/);
  const text = notice.closest("p")!.textContent!;
  expect(text).toMatch(/split it into smaller files/i);
  expect(text).toMatch(/ok\.pdf/);
});
