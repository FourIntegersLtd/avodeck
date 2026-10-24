# Web app

Read the root `AGENTS.md` first. This file covers `web/`.

## The one rule

The frontend renders screens and sends user actions to FastAPI. Nothing else.

- Call only the generated API client in `src/foundation/platform-client/` (from OpenAPI). No `fetch` to any other origin.
- No Supabase, Azure or AWS SDKs, keys or URLs. CI fails the build if any appear.
- No business logic that the server must enforce (blind read, ownership, locking, provenance). The frontend may mirror it for display, never replace it.
- Auth is a cookie set by FastAPI. Do not store tokens in `localStorage`, `sessionStorage` or JS memory.
- Send the CSRF token header on every state-changing request (the client does this).
- Every edit sends `If-Match` with the ETag it loaded; a 412 shows the "which version do you want to keep?" prompt, never a silent overwrite. Create, send, sign, serve and pay actions send an `Idempotency-Key` (the client does this), so a double click does one thing.
- Show what the server allows: the case's permissions for this person come from `GET /cases/{id}` (`allowed_actions`); hide or disable the rest, but the server still decides.
- Today and lists span every organisation the person belongs to, with an organisation badge and filter.
- Exceptions: the signature pad (draws on a canvas, then uploads a PNG) and the offline witness-box package (screen 25).

## Design system

Components in `src/foundation/ui/` match the design canvas (`docs/spec/design-canvas.md`). Foundation-owned; request changes, don't fork them.

- **Colours:** Forest `#0F3D2C`, Green `#1E6B4E` (primary), Leaf `#2F9E6E`, Mint `#E6F2EC`, Amber `#B45309` / tint `#FDF1E3` / border `#E8C9A6` / text `#8A3F06`, Ink `#16201B`, Slate `#4F5953`, Line `#DDE3DF`, Surface `#F6F7F5`, Stop `#B42318`, Highlighter `#FFF3B0`.
- **Record source colours:** come from the specialty pack's source types (`sources.yaml`); at launch primary_care_record `#1E6B4E`, hospital `#4C6E91`, out_of_hours `#2C8C99`, results `#7A6BA8`, letters `#8C7556`. Never key colours by name in code.
- **Fonts:** Atkinson Hyperlegible (UI), IBM Plex Mono (page numbers and dates), Georgia (report and record text).
- **Sizes:** body 17px, buttons at least 52px tall, one primary action per screen, bottom right.
- **Status badges:** "Checked by you" (solid, tick), "Please check" (dashed 2px `#8A948E`), "Not right", "Thing to look at" (amber).
- Calm look (DesignSystemCalm board): primary 56px green (one per screen); `secondary` is a soft grey fill (#F1F2F1, no border); `outline` only for Done or Cancel in a bar; `quiet`, `link`, `danger` (soft red); cards radius 16 with a soft shadow and no border; paper radius 3; the step bar is a grey track with the current step raised in white. New parts: `SegmentedControl`, `StepperCapsule`, `Menu` (`MenuSection`, `MenuItem`), `ListRow`, `StatusMark`, and `Notice` tones `amber` and `mint`.
- Components: `Button`, `CaseHeader` (step pills), `Card`, `Paper`, `PageRef`, `StatusBadge`, `DictationBox`, `Toggle`, `ChoiceCards`, `Countdown`, `AmberNotice`, `Table`.

## Copy

Every string uses an i18n key (`src/foundation/i18n/en-GB.json` is the base; `en-AU`, `en-NZ`, `en-CA` will be small overrides). Words that change by pack (party roles, instructing party, clinician title, court bundle name, step and screen labels) come from the merged pack vocabulary returned by `GET /cases/{id}/pack` (or `GET /jurisdictions/{code}` outside a case). Dates and deadlines show in the expert's timezone. Dates, numbers and currency use `Intl` with the expert's locale. Use `docs/spec/glossary.md`. Plain words for experienced clinicians who are not confident with computers (launch users: GPs aged 50–60). No jargon.

## Structure

Same layering as osceguide's web app. Imports go one way only:
`app → surfaces → domains → capabilities → foundation`. A capability never imports a domain or a surface.

```
src/
  app/                       # routes and layouts; (auth)/ holds S01 sign-in (foundation)
  foundation/                # foundation-owned
    ui/                      # design system components
    platform-client/         # generated API client + CSRF/ETag/Idempotency wrapper
    i18n/  lib/
    guards/                  # non-negotiable tests, e.g. no forbidden hosts, no token storage
  capabilities/<feature>/    # screens and components for one slice, tests beside them (*.test.tsx)
                             # foundation: account (S28), today (S03)
e2e/<feature>/               # Playwright acceptance tests
```

`domains/` and `surfaces/` come in only when Avodeck has more than one arm (for example the organisation view in slice O). Until then, keep everything in `capabilities/`.

## Using the shell (foundation, step 9)

- Call the API only through `api` and `unwrap()` from `foundation/platform-client` (cookie session, `X-CSRF-Token` from the `av_csrf` cookie, `ApiError {status, code, extra}`; `idempotencyKey()`, `ifMatch()`, `websocketUrl()`). Show words from the error `code` (`foundation/lib/errors.ts`), never the API's message.
- Words: `t(key, params)` from `foundation/i18n`; a slice adds its keys with `registerMessages()`. Plurals use `_one`/`_other`. Dates: `formatDue`, `formatDay`, `formatRelative` with the person's locale, else the pack's.
- Today: a slice's `NeedsYouItem` shows its `label_key`/`action_key` words once registered, and its button opens the screen registered with `registerScreen(key, ({caseId, params}) => path)` (`foundation/lib/screens.ts`). Unregistered items show "{{case}} needs you" and open the case.
- Risky actions: wrap the call in `useStepUp()` (rule 10); it asks for the code on `step_up_required` and tries again.
- Signed-in screens read the person with `useSession()`. Routes: export a `RouteObject[]` named `<name>Routes` from the capability's `index.tsx`; `app/capabilityRoutes.ts` mounts every one behind the signed-in guard (no edit to `app/routes.tsx`).
- Tests: `renderAt(path)`, `signedIn()`, `apiError()` from `src/test/`; pack data from `launchPack`/`fixturePack` (generated by `make api-client` from the real packs). Write no pack word (party names, clinician titles) in a capability, tests included.
- The production build writes the CSP into `index.html` from `VITE_API_ORIGIN` (`web/csp.ts`); no inline styles or scripts.
- `web/vercel.json` sends `frame-ancestors 'none'`, `X-Frame-Options: DENY`, nosniff and no referrer on every path (a meta CSP cannot forbid framing). Until stage 2, the only supported setup serves the API on the web app's own origin (same-origin proxy, `VITE_API_ORIGIN` empty): the session cookie is `SameSite=Strict` and the API refuses other origins. A separate API origin needs a decision record first.

## Testing

- Vitest + Testing Library for components; Playwright for each screen's acceptance tests; axe in Playwright on every screen.
- Mock the API with MSW using fixtures generated from the OpenAPI schema.
- Write the Playwright acceptance test from the screen spec before building the screen.
