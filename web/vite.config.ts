/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The browser talks only to FastAPI (rule 4). In development the API runs on :8000 and Vite
// forwards /v1 to it, so the web app and the API share one origin and the session cookie.
const API_DEV = process.env.API_DEV_ORIGIN ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/v1": { target: API_DEV, ws: true } },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    coverage: { provider: "v8", include: ["src/**/*.{ts,tsx}"], exclude: ["src/**/*.test.*", "src/test/**", "src/foundation/platform-client/schema.ts"] },
  },
});
