// A real browser, the production build and its CSP, hashing and
// chunking a real 121 MB bundle. vitest stubs the network and jsdom cannot run a Worker, so no
// automated test has ever moved a large file through the real client. Chunk PUTs are stubbed: the
// network is covered by the API tests; what is under test here is the browser side.
import { existsSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { CASE_A, PACKS, stubPipeline } from "./helpers";

const UP = "a6666666-6666-4666-8666-666666666666";
const DOC = "b7777777-7777-4777-8777-777777777777";
const JOB = "77777777-7777-4777-8777-777777777777";

// Not in the repository (a real bundle): set AV_LARGE_BUNDLE, AV_LARGE_BUNDLE_SIZE and
// AV_LARGE_BUNDLE_SHA256 to run it. Skipped everywhere else, CI included.
const FILE = process.env.AV_LARGE_BUNDLE ?? "";
const SIZE = Number(process.env.AV_LARGE_BUNDLE_SIZE ?? 0);
const SHA = process.env.AV_LARGE_BUNDLE_SHA256 ?? "";
const CHUNK = 8 * 1024 * 1024;
const COUNT = Math.ceil(SIZE / CHUNK);

test("a real 121 MB bundle hashes and chunks in the built page", async ({ page }) => {
  test.skip(!FILE || !existsSync(FILE) || !SIZE || !SHA, "set AV_LARGE_BUNDLE, AV_LARGE_BUNDLE_SIZE and AV_LARGE_BUNDLE_SHA256");
  test.setTimeout(600_000);

  const violations: string[] = [];
  page.on("console", (m) => {
    if (/Content Security Policy|WebAssembly|out of memory/i.test(m.text())) violations.push(m.text());
  });
  page.on("crash", () => violations.push("PAGE CRASHED"));
  page.on("pageerror", (e) => violations.push(`pageerror: ${e.message}`));

  await stubPipeline(page, PACKS[0]!.pack);
  const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

  const seen: { started?: Record<string, unknown>; parts: Map<number, number>; hashes: Set<string>; complete: number; startedAt?: number; hashedAt?: number; doneAt?: number } =
    { parts: new Map(), hashes: new Set(), complete: 0 };

  await page.route(`**/v1/cases/${CASE_A}/uploads`, async (route) => {
    seen.started = route.request().postDataJSON();
    seen.hashedAt = Date.now();
    await route.fulfill(json({ upload_id: UP, case_id: CASE_A, status: "open", size: SIZE, chunk_size: CHUNK, chunk_count: COUNT, received: [], missing: Array.from({ length: COUNT }, (_, i) => i + 1), document_id: null }, 201));
  });
  await page.route(`**/v1/uploads/${UP}/chunks/*`, async (route) => {
    const n = Number(new URL(route.request().url()).pathname.split("/").pop());
    const body = route.request().postDataBuffer();
    seen.parts.set(n, body ? body.length : -1);
    const h = route.request().headers()["x-chunk-sha256"];
    if (h) seen.hashes.add(h);
    await route.fulfill(json({ received: [...seen.parts.keys()].sort((a, b) => a - b) }));
  });
  await page.route(`**/v1/uploads/${UP}/complete`, async (route) => {
    seen.complete += 1;
    await route.fulfill(json({ status: "finalizing" }, 202));
  });
  await page.route(`**/v1/uploads/${UP}`, (route) =>
    route.fulfill(json({ upload_id: UP, case_id: CASE_A, status: "complete", size: SIZE, chunk_size: CHUNK, chunk_count: COUNT, received: Array.from({ length: COUNT }, (_, i) => i + 1), missing: [], document_id: DOC })),
  );
  await page.route(`**/v1/cases/${CASE_A}/documents/${DOC}`, (route) => route.fulfill(json({})));
  await page.route(`**/v1/cases/${CASE_A}/documents/append`, (route) => route.fulfill(json({ job_id: JOB }, 202)));

  await page.goto(`/cases/${CASE_A}/papers/add`);
  await page.locator('input[type="file"]').setInputFiles(FILE);
  seen.startedAt = Date.now();
  await page.getByRole("button", { name: "Add these papers" }).click();

  // Hashing: the start call carries the whole-file hash the browser computed.
  await expect.poll(() => seen.started?.sha256, { timeout: 480_000, intervals: [1000] }).toBe(SHA);
  const hashMs = seen.hashedAt! - seen.startedAt!;

  await expect(page.getByRole("heading", { level: 1, name: "Getting your new papers ready" })).toBeVisible({ timeout: 300_000 });
  seen.doneAt = Date.now();

  const cdp = await page.context().newCDPSession(page);
  const usage = (await cdp.send("Runtime.getHeapUsage")) as { usedSize: number; totalSize: number };
  const heap = { usedJSHeapSize: usage.usedSize, jsHeapSizeLimit: usage.totalSize };
  const total = [...seen.parts.values()].reduce((a, b) => a + b, 0);
  console.log(JSON.stringify({
    size: SIZE, chunks_expected: COUNT, chunks_sent: seen.parts.size, bytes_sent: total,
    last_chunk: seen.parts.get(COUNT), distinct_part_hashes: seen.hashes.size, completes: seen.complete,
    hash_seconds: +(hashMs / 1000).toFixed(1), total_seconds: +((seen.doneAt - seen.startedAt!) / 1000).toFixed(1),
    heap_mb: +(heap.usedJSHeapSize / 1048576).toFixed(0),
    heap_total_mb: +(heap.jsHeapSizeLimit / 1048576).toFixed(0),
    violations,
  }, null, 2));

  expect(seen.started?.size).toBe(SIZE);
  expect(seen.parts.size).toBe(COUNT);
  expect(total).toBe(SIZE);
  expect(seen.parts.get(COUNT)).toBe(SIZE - (COUNT - 1) * CHUNK);
  expect(seen.hashes.size).toBe(COUNT); // every part carried its own hash
  expect(seen.complete).toBe(1);
  expect(violations).toEqual([]);
});
