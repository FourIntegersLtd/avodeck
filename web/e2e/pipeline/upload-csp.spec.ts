// The production Content-Security-Policy (script-src 'self', no 'wasm-unsafe-eval') must not stop an upload at
// its hashing step. jsdom cannot see this: only a real browser, on the built page, enforces the policy.
import { expect, test } from "@playwright/test";
import { CASE_A, PACKS, stubPipeline } from "./helpers";

const UP = "a6666666-6666-4666-8666-666666666666";
const DOC = "b7777777-7777-4777-8777-777777777777";
const JOB = "77777777-7777-4777-8777-777777777777";
const SHA_OF_HELLO = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";

test("the built page hashes and uploads a file under its own CSP", async ({ page }) => {
  const csp = await (await page.request.get("/")).text();
  expect(csp).toContain("Content-Security-Policy");
  expect(csp).not.toContain("wasm-unsafe-eval");

  const violations: string[] = [];
  page.on("console", (m) => {
    if (/Content Security Policy|WebAssembly/i.test(m.text())) violations.push(m.text());
  });
  await stubPipeline(page, PACKS[0]!.pack);
  const seen: { started?: Record<string, unknown>; complete: number } = { complete: 0 };
  const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
  await page.route(`**/v1/cases/${CASE_A}/uploads`, async (route) => {
    seen.started = route.request().postDataJSON();
    await route.fulfill(json({ upload_id: UP, case_id: CASE_A, status: "open", size: 5, chunk_size: 8388608, chunk_count: 1, received: [], missing: [1], document_id: null }, 201));
  });
  await page.route(`**/v1/uploads/${UP}/chunks/1`, (route) => route.fulfill(json({ received: [1] })));
  await page.route(`**/v1/uploads/${UP}/complete`, async (route) => {
    seen.complete += 1;
    await route.fulfill(json({ status: "finalizing" }, 202));
  });
  await page.route(`**/v1/uploads/${UP}`, (route) =>
    route.fulfill(json({ upload_id: UP, case_id: CASE_A, status: "complete", size: 5, chunk_size: 8388608, chunk_count: 1, received: [1], missing: [], document_id: DOC })),
  );
  await page.route(`**/v1/cases/${CASE_A}/documents/${DOC}`, (route) => route.fulfill(json({})));
  await page.route(`**/v1/cases/${CASE_A}/documents/append`, (route) => route.fulfill(json({ job_id: JOB }, 202)));

  await page.goto(`/cases/${CASE_A}/papers/add`);
  await page.locator('input[type="file"]').setInputFiles({ name: "records.pdf", mimeType: "application/pdf", buffer: Buffer.from("hello") });
  await page.getByRole("button", { name: "Add these papers" }).click();

  await expect.poll(() => seen.started?.sha256, { timeout: 15_000 }).toBe(SHA_OF_HELLO);
  await expect(page.getByRole("heading", { level: 1, name: "Getting your new papers ready" })).toBeVisible();
  expect(violations).toEqual([]);
});
