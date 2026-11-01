import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { launchJurisdiction } from "./fixtures";

// The API is mocked per test with MSW (web/AGENTS.md); unhandled requests fail the test.
// `/v1/jurisdictions*` is kept as a baseline default (not reset between tests, like the rest of the
// mocks): account-level screens such as the legal gate read jurisdiction-driven words from it with no
// case in play, so every test gets it for free instead of every caller having to mock it.
export const server = setupServer(
  http.get("/v1/jurisdictions", () => HttpResponse.json([{ code: launchJurisdiction.code, name: launchJurisdiction.name, version: launchJurisdiction.version }])),
  http.get("/v1/jurisdictions/:code", () => HttpResponse.json(launchJurisdiction)),
);
