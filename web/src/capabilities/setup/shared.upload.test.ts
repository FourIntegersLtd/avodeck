// Uploads send each part with a few patient retries (a network drop or a 5xx), never for a refusal, and a
// batch carries on past a file that fails, reporting which were saved and which not, by name.
import { expect, test } from "vitest";
import { http, HttpResponse, json, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { uploadFile, uploadFiles } from "./shared";

const UP = "a6666666-6666-4666-8666-666666666666";
const file = (name: string) => new File([new Uint8Array(10)], name, { type: "application/pdf" });

function server_(put: () => Response, started: () => Response = () => json({ upload_id: UP, chunk_size: 8_388_608, chunk_count: 1 }, 201)) {
  server.use(
    http.post("/v1/cases/:caseId/uploads", started),
    http.put("/v1/uploads/:id/chunks/:n", put),
    http.post("/v1/uploads/:id/complete", () => json({ document_id: "b7777777-7777-4777-8777-777777777777" })),
  );
}

test("a part that fails with a 5xx is sent again and the upload completes", async () => {
  let tries = 0;
  server_(() => (++tries < 3 ? HttpResponse.json({ error: { code: "server_error" } }, { status: 503 }) : new HttpResponse(null, { status: 204 })));
  await expect(uploadFile(CASE_A, file("a.pdf"), "records")).resolves.toBe("b7777777-7777-4777-8777-777777777777");
  expect(tries).toBe(3);
});

test("a dropped connection on a part is retried", async () => {
  let tries = 0;
  server_(() => (++tries < 2 ? HttpResponse.error() : new HttpResponse(null, { status: 204 })));
  await uploadFile(CASE_A, file("a.pdf"), "records");
  expect(tries).toBe(2);
});

test("a part is tried up to three more times and then the failure is thrown", async () => {
  let tries = 0;
  server_(() => (++tries, HttpResponse.json({ error: { code: "server_error" } }, { status: 500 })));
  await expect(uploadFile(CASE_A, file("a.pdf"), "records")).rejects.toMatchObject({ status: 500 });
  expect(tries).toBe(4);
});

test("a refusal (4xx) is not retried", async () => {
  let tries = 0;
  server_(() => (++tries, HttpResponse.json({ error: { code: "chunk_corrupted" } }, { status: 400 })));
  await expect(uploadFile(CASE_A, file("a.pdf"), "records")).rejects.toMatchObject({ code: "chunk_corrupted" });
  expect(tries).toBe(1);
});

test("a batch carries on after one file fails and names each, with the reason", async () => {
  let n = 0;
  server_(
    () => new HttpResponse(null, { status: 204 }),
    () => (++n === 1 ? HttpResponse.json({ error: { code: "file_too_large" } }, { status: 413 }) : json({ upload_id: UP, chunk_size: 8_388_608, chunk_count: 1 }, 201)),
  );
  const seen: string[] = [];
  const out = await uploadFiles(CASE_A, [file("big.pdf"), file("ok.pdf")], "records", async (f) => void seen.push(f.name));
  expect(out.saved.map((s) => s.file.name)).toEqual(["ok.pdf"]);
  expect(out.failed).toHaveLength(1);
  expect(out.failed[0]!.name).toBe("big.pdf");
  expect(out.failed[0]!.reason).toMatch(/split it into smaller files/i);
  expect(seen).toEqual(["ok.pdf"]);
});
