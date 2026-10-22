# Design diff

Proves a built screen matches its canvas artboard with measurements, not by eye.

**Method.** `run.mjs` renders the artboard (`design/canvas/project/<id>.dc.html`, with the canvas fonts served from
`@fontsource`) and the live page in headless Chromium at the artboard's size with reduced motion, waits for fonts,
screenshots both, and writes to `scripts/design-diff/out/` (git-ignored): `<id>.artboard.png`, `<id>.page.png`,
`<id>.diff.png` and `<id>.json`. The JSON has the mismatch percentage, the eight most mismatched 80px grid cells,
the rect of every probe on both sides, and every probe whose rect, font family, size, weight, line height, colour,
background, padding or corner radius differs by more than 2px. Exit 1 when a screen is over budget (0.5%) or any
probe differs.

**Run.** Start the app (`pnpm dev:next`, port 3001, or `pnpm build:next && pnpm start:next`), then
`pnpm design-diff --base http://127.0.0.1:3001 S01-SignIn` (no ids: every screen). If the Playwright browser is not
installed, set `CHROMIUM_PATH` to a Chromium binary.

**Add a screen.** Copy `screens/S01-SignIn.json` to `screens/<id>.json`:

| Field | Meaning |
|---|---|
| `id`, `artboard`, `route` | screen id, artboard file name, route of the live page |
| `width`, `height` | the artboard's `$preview` size |
| `session` | `true` signs the page in with the standard mocks in `mocks.mjs` |
| `mock` | `{ "/v1/path": json }` extra canned API answers for the state drawn (Playwright URL globs) |
| `setup` | steps to reach the drawn state: `{fill, value}`, `{click}`, `{waitFor}`, `{blur: true}` |
| `artboardRemove`, `pageRemove` | selectors to strip noise (the "Design notes" callout, banners, floating widgets) |
| `probes` | `{name, artboard, page, allow?}` selector pairs to measure; `allow` lists props that deliberately differ |
| `artboardCss` | extra CSS for the artboard only, for a deliberate deviation (explain it in `deviations`) |
| `deviations` | every deliberate difference from the artboard, with its reason |
| `budget`, `budgetReason` | a per-screen mismatch budget, only with a reason |

Defaults for every screen are in `screens.json` (budget, the 17px text rule and its deviation note).

**Read a failure.**
1. Fix the probe findings first: they name the element, the property and both values. A finding on a container
   usually explains the ones below it (everything shifts). Fix the top-most one and re-run.
2. Then open `<id>.diff.png`: red is a pixel that differs. Look at the hot cells.
3. Uniform low noise across text with no probe findings is anti-aliasing; that is what the budget is for.
4. A screen is done when it prints PASS. "Looks right" is not a result.

**Traps.** Canvas inline styles cannot ship (the production CSP is `style-src 'self'` plus a nonce), so every canvas
value becomes a class in the component's stylesheet or a Tailwind utility with the exact value. Tailwind's reset is
off until the last Vite-era screen is moved. Text under 17px is raised to 17px (nfr U2). The sign-in endpoint is
rate limited, so authed screens use mocked API answers; never point this at a real account.
