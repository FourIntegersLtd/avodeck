import { setupServer } from "msw/node";

// The API is mocked per test with MSW (web/AGENTS.md); unhandled requests fail the test.
export const server = setupServer();
