---
name: add-screen
description: Build an Avodeck screen from its design canvas board using the design system, plain-language copy and accessibility checks.
---

# Add a screen

1. **Find the board** for the screen in `docs/spec/design-canvas.md` (screen number, file name). Look at `docs/design/images/<File>.png`, then read `docs/design/screens/<File>.dc.html` for exact wording, sizes and colours. Match layout, hierarchy and wording; build with design-system components, never by copying the mock-up markup. Board words like "solicitor" or "GP" come from the pack vocabulary.
2. **Acceptance test first:** a Playwright test in `web/e2e/<feature>/<screen>.spec.ts` describing what the expert can see and do, plus an axe check. Mock the API with MSW fixtures from OpenAPI.
3. **Build** in `web/src/capabilities/<feature>/`:
   - Use design-system components only (`Button`, `CaseHeader`, `Card`, `Paper`, `PageRef`, `StatusBadge`, `DictationBox`, …). Need a new shared component? Request it from the foundation owner.
   - Data only via the generated client in `src/api/`.
   - One primary action, bottom right. Body text 17px. Buttons at least 52px.
   - Copy from `docs/spec/glossary.md`, always through i18n keys. Pack words (party roles, instructing party, clinician title, court bundle name) come from the merged pack vocabulary. Check the screen under the fixture packs too. Plain words; no jargon.
   - Page references in IBM Plex Mono via `PageRef`.
4. **States:** loading, empty, error (plain-English message, no technical codes), and locked/blind-read states where relevant.
5. **Never** hide something for safety reasons only in the UI; the server must already enforce it.
6. **Register "needs you" items** for the Today screen through the shared interface if the slice has any.
7. **Check:** Playwright + axe green; keyboard navigation works; colour is never the only signal.
