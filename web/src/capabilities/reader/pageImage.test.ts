// A page's picture is shown from an object URL (no base64 copy in memory), the last few pages are kept so
// flipping back does not fetch and decrypt the page again, and a URL is released as soon as it leaves the cache.
// Nothing is written to disk: the server's no-store stays honoured.
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { http, HttpResponse, server } from "../../test/api";
import { CASE_A } from "../../test/fixtures";
import { KEEP_PAGES, forgetPage, forgetPages, pageImage } from "./pageImage";

let made = 0;
const fetched: number[] = [];

beforeEach(() => {
  made = 0;
  fetched.length = 0;
  forgetPages();
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => `blob:page-${++made}`), revokeObjectURL: vi.fn() }));
  server.use(
    http.get("/v1/cases/:caseId/pages/:pageNo/image", ({ params }) => {
      fetched.push(Number(params.pageNo));
      return new HttpResponse(new Uint8Array([255, 216, 255]), { headers: { "content-type": "image/jpeg" } });
    }),
  );
});
afterEach(() => {
  forgetPages();
  vi.unstubAllGlobals();
});

test("shows a page as an object URL made from the picture the API streams", async () => {
  expect(await pageImage(CASE_A, 7)).toBe("blob:page-1");
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  expect((vi.mocked(URL.createObjectURL).mock.calls[0]![0] as Blob).type).toBe("image/jpeg");
  expect(fetched).toEqual([7]);
});

test("a page seen a moment ago is shown again without another fetch, also when asked for twice at once", async () => {
  const [a, b] = await Promise.all([pageImage(CASE_A, 7), pageImage(CASE_A, 7)]);
  expect(a).toBe("blob:page-1");
  expect(b).toBe("blob:page-1");
  expect(await pageImage(CASE_A, 7)).toBe("blob:page-1");
  expect(fetched).toEqual([7]);
});

test("keeps only the last few pages and releases the oldest when one more arrives", async () => {
  for (let page = 1; page <= KEEP_PAGES; page++) await pageImage(CASE_A, page);
  expect(URL.revokeObjectURL).not.toHaveBeenCalled();
  await pageImage(CASE_A, 1); // page 1 is used again, so page 2 is now the oldest
  await pageImage(CASE_A, KEEP_PAGES + 1);
  expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:page-2");
  expect(await pageImage(CASE_A, 1)).toBe("blob:page-1");
  expect(fetched.filter((p) => p === 1)).toEqual([1]);
  expect(fetched.filter((p) => p === 2)).toEqual([2]);
  await pageImage(CASE_A, 2);
  expect(fetched.filter((p) => p === 2)).toEqual([2, 2]);
});

test("the same page number in another case is another picture", async () => {
  await pageImage(CASE_A, 7);
  await pageImage("22222222-2222-4222-8222-222222222222", 7);
  expect(fetched).toEqual([7, 7]);
});

test("a page the API cannot give is not kept, so the next look asks again", async () => {
  server.use(http.get("/v1/cases/:caseId/pages/:pageNo/image", () => HttpResponse.json({ code: "not_found" }, { status: 404 })));
  await expect(pageImage(CASE_A, 9)).rejects.toThrow();
  server.use(http.get("/v1/cases/:caseId/pages/:pageNo/image", () => new HttpResponse(new Uint8Array([1]), { headers: { "content-type": "image/jpeg" } })));
  expect(await pageImage(CASE_A, 9)).toBe("blob:page-1");
});

test("forgetting the pages releases every URL", async () => {
  await pageImage(CASE_A, 1);
  await pageImage(CASE_A, 2);
  forgetPages();
  expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
  await pageImage(CASE_A, 1);
  expect(fetched).toEqual([1, 2, 1]);
});

test("forgetting one page releases its URL and the next look fetches it again", async () => {
  await pageImage(CASE_A, 1);
  await pageImage(CASE_A, 2);
  forgetPage(CASE_A, 1);
  expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:page-1");
  await pageImage(CASE_A, 2);
  expect(fetched).toEqual([1, 2]);
  await pageImage(CASE_A, 1);
  expect(fetched).toEqual([1, 2, 1]);
  forgetPage(CASE_A, 99); // a page never seen is ignored
  expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
});
