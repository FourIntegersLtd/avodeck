// S04 design match: each uploaded file shows its page count before anything is processed. The count comes
// from the case's file list (read from the PDF's page tree); a file with no count shows no pages.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { CASE_B, me, TEAM_ORG } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { caseItem, casePack, combination, jurisdiction } from "./testing";

const DOCS = ["b7777777-7777-4777-8777-777777777771", "b7777777-7777-4777-8777-777777777772", "b7777777-7777-4777-8777-777777777773"];

function open(counts: (number | null)[]) {
  let next = 0;
  signedIn({ me: me(), pack: casePack("launch") });
  server.use(
    http.get("/v1/packs", () => json([combination("launch")])),
    http.get("/v1/jurisdictions/:code", () => json(jurisdiction("launch"))),
    http.post("/v1/cases", () => HttpResponse.json(caseItem("launch", { id: CASE_B, organisation_id: TEAM_ORG }), { status: 201, headers: { etag: '"1"' } })),
    http.post("/v1/cases/:caseId/uploads", () => json({ upload_id: "a6666666-6666-4666-8666-666666666666", chunk_size: 8_388_608, chunk_count: 1 }, 201)),
    http.put("/v1/uploads/:id/chunks/:n", () => new HttpResponse(null, { status: 204 })),
    http.post("/v1/uploads/:id/complete", () => json({ document_id: DOCS[next++] })),
    http.post("/v1/cases/:caseId/instructions", () => json({ id: "b1111111-1111-4111-8111-111111111111", kind: "letter", version_no: 1, document_id: DOCS[0], job_id: "c8888888-8888-4888-8888-888888888888", locked: false, received_on: null, reopens_questions: false }, 201)),
    http.post("/v1/cases/:caseId/prefill", () => json({ job_id: "d9999999-9999-4999-8999-999999999999" }, 202)),
    http.get("/v1/cases/:caseId/prefill", () => json({ reading: true, rejected: 0, suggestions: [] })),
    http.get("/v1/cases/:caseId/files", () =>
      json(counts.map((page_count, i) => ({ document_id: DOCS[i], filename: `file${i}.pdf`, kind: "records", page_count }))),
    ),
  );
}

const file = (name: string) => new File([new Uint8Array(10)], name, { type: "application/pdf" });
const zone = (title: string) => screen.getByText(title).closest(".av-dropzone") as HTMLElement;
const input = (title: string) => zone(title).querySelector("input[type=file]") as HTMLInputElement;

test("the letter shows its name and page count", async () => {
  open([4]);
  const { user } = renderAt("/cases/new");
  await user.type(await screen.findByLabelText("A short name for the case"), "Mrs B");
  await user.upload(input("Letter of instruction"), file("letter.pdf"));
  const added = zone("Letter of instruction");
  expect(await within(added).findByText("letter.pdf")).toBeInTheDocument();
  expect(await within(added).findByText("4 pages")).toBeInTheDocument();
});

test("records show the number of files and the pages across them", async () => {
  open([1000, 268]);
  const { user } = renderAt("/cases/new");
  await user.type(await screen.findByLabelText("A short name for the case"), "Mrs B");
  await user.upload(input("Medical records"), [file("a.pdf"), file("b.pdf")]);
  expect(await within(zone("Medical records")).findByText("2 files · 1,268 pages")).toBeInTheDocument();
});

test("when a file's pages are unknown only the file count shows", async () => {
  open([10, null]);
  const { user } = renderAt("/cases/new");
  await user.type(await screen.findByLabelText("A short name for the case"), "Mrs B");
  await user.upload(input("Medical records"), [file("a.pdf"), file("b.docx")]);
  await waitFor(() => expect(within(zone("Medical records")).getByText("2 files")).toBeInTheDocument());
  expect(within(zone("Medical records")).queryByText(/pages/)).toBeNull();
});
