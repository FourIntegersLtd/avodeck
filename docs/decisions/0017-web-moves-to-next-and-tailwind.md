# 0017 The web app moves from Vite to Next.js and Tailwind

Date: 2026-10-19 · Status: proposed (needs the owner's approval; `web/` build files and `.github/` are foundation-owned)

**Problem.** `web/` was scaffolded as Vite + React + react-router with one hand-written stylesheet (`foundation/ui/styles.css`). The owner's other products (eventtz, osceguide) use Next.js and Tailwind with shared components, and the design canvas is easiest to reproduce exactly when every value is a utility class beside the markup. Two stacks mean two sets of conventions, tooling and CI.

**Decision.**
- Next.js 16 (App Router) and Tailwind CSS v4 (as eventtz; osceguide is still on v3). Design tokens (colours, type sizes, radii, spacing from `web/AGENTS.md` and the canvas) go in one `@theme` block.
- The folder tiers stay as `web/AGENTS.md` already describes (`app → capabilities → foundation`, same as osceguide). Only `src/app/` changes: react-router routes become App Router routes. `src/capabilities/*` and `src/foundation/*` keep their paths and tests.
- Every screen is a client-side screen that calls only the generated API client (rule 4). No server components that fetch data, no server actions, no Next API routes, no secrets in the Next runtime. The API is reached through a same-origin rewrite to FastAPI, as now.
- The production CSP stays strict: no inline styles, and scripts only with a per-request nonce set in `middleware.ts`.
- The offline witness-box screen (S25) must still work with no connection (rule 4).
- The guard tests in `web/src/foundation/guards/` keep their meaning; any wording tied to Vite is updated in the same pull request, never weakened.

**Order.**
1. This record, approved.
2. Scaffold Next and Tailwind beside the Vite app; move `foundation/ui` to utilities; same unit tests.
3. Move routes one capability at a time, behind the existing tests.
4. Playwright and CI to the Next build; delete Vite.

**Consequences.**
- Every screen file changes, so this waits until the open canvas-match pull request (#56) and the server-data pull request have merged.
- The design diff harness (`web/scripts/design-diff/`) needs only a route and a port, so it is unaffected by the move.
- `vercel.json`, `csp.ts`, `devMfa.ts` and the e2e web server command are rewritten.
