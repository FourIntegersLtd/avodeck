// S02 signature pad: drawn on the device, sent to FastAPI as a PNG image (rule 4). jsdom has no canvas, so the
// canvas context and toBlob are stubbed.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { http, HttpResponse, json, server, signedIn } from "../../test/api";
import { me } from "../../test/fixtures";
import { renderAt } from "../../test/render";
import { casePack, combination, jurisdiction } from "./testing";

const PNG = [0x89, 0x50, 0x4e, 0x47];
const ctx = { lineWidth: 0, lineCap: "", beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), clearRect: vi.fn() };

beforeEach(() => {
  Object.values(ctx).forEach((f) => typeof f === "function" && f.mockClear());
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((cb) => cb(new Blob([new Uint8Array(PNG)], { type: "image/png" })));
});
afterEach(() => vi.restoreAllMocks());

function open() {
  const pack = casePack("launch");
  const j = jurisdiction("launch");
  signedIn({ me: me(), pack });
  server.use(
    http.get("/v1/me/profile", () =>
      HttpResponse.json(
        { full_name: "Dr Jane Morgan", profession: pack.profession, registration_body: pack.registration_bodies[0], registration_number: "1234567", specialties: [combination("launch").specialty.code], default_jurisdiction: j.code, default_retention: null, hourly_rate: null, currency: null },
        { headers: { etag: '"1"' } },
      ),
    ),
    http.get("/v1/packs", () => json([combination("launch")])),
    http.get("/v1/jurisdictions", () => json([combination("launch").jurisdiction])),
    http.get("/v1/jurisdictions/:code", () => json(j)),
    http.get("/v1/me/roles", () => json([])),
    http.get("/v1/me/training", () => json([])),
    http.get("/v1/me/template", () => HttpResponse.json({ error: { code: "not_found" } }, { status: 404 })),
    http.get("/v1/me/declaration", () => HttpResponse.json({ jurisdiction: j.code, wording: pack.declarations, additions: [], locked: false }, { headers: { etag: '"1"' } })),
    http.get("/v1/me/signature/image", () => HttpResponse.json({ error: { code: "not_found" } }, { status: 404 })),
    http.get("/v1/me/stats/balance", () => json({ counts: { other_side: 0, instructing_side: 0, joint: 0 }, months: 12 })),
  );
  return renderAt("/profile");
}

async function draw(region: HTMLElement) {
  const pad = within(region).getByRole("img", { name: "Box to draw your signature in" });
  fireEvent.pointerDown(pad, { clientX: 10, clientY: 10 });
  fireEvent.pointerMove(pad, { clientX: 40, clientY: 30 });
  fireEvent.pointerUp(pad);
}

test("drawing then saving sends a PNG to the API", async () => {
  let sent: { method: string | null; type: string | null; bytes: number[] } | null = null;
  const { user } = open();
  server.use(
    http.post("/v1/me/signature", async ({ request }) => {
      sent = {
        method: new URL(request.url).searchParams.get("method"),
        type: request.headers.get("content-type"),
        bytes: [...new Uint8Array(await request.arrayBuffer())],
      };
      return json({ id: "c4444444-4444-4444-8444-444444444444", method: "drawn", width: 600, height: 200, created_at: "2026-10-17T09:00:00Z" }, 201);
    }),
  );
  const region = await screen.findByRole("region", { name: "Declaration and signature" });
  await user.click(within(region).getByRole("button", { name: /Add your signature/ }));
  await draw(region);
  expect(ctx.lineTo).toHaveBeenCalled();
  await user.click(within(region).getByRole("button", { name: "Save this signature" }));
  await waitFor(() => expect(sent).not.toBeNull());
  expect(sent!.method).toBe("drawn");
  expect(sent!.type).toBe("image/png");
  expect(sent!.bytes).toEqual(PNG);
  expect(await within(region).findByText("Your signature is saved.")).toBeInTheDocument();
});

test("clearing wipes the pad and switches saving off until you draw again", async () => {
  const { user } = open();
  const region = await screen.findByRole("region", { name: "Declaration and signature" });
  await user.click(within(region).getByRole("button", { name: /Add your signature/ }));
  const save = within(region).getByRole("button", { name: "Save this signature" });
  expect(save).toBeDisabled();
  await draw(region);
  expect(save).toBeEnabled();
  await user.click(within(region).getByRole("button", { name: "Start again" }));
  expect(ctx.clearRect).toHaveBeenCalledWith(0, 0, 600, 200);
  expect(save).toBeDisabled();
});
