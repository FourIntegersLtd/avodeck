# 0011 Overlay layer, pack inheritance, rule types and fixture packs

Date: 2026-10-16 · Status: accepted

**Context.** An audit of the architecture found things that depend on country *and* specialty together but sat in only one layer:
- GP record systems (EMIS and SystmOne are UK systems);
- guidance bodies (NICE);
- the clinician's local title ("GP" in the UK, "family physician" in Canada);
- drug-monitoring tables and dictation phrase lists.

It also found UK, GP and clinical-negligence words in table, endpoint and field names, and missing extension points: time zones, court calendars, professions, parties, instructing-party types, pack inheritance, and prompts built from packs.

**Decision.**
1. **Four layers:** jurisdiction → case type → specialty → overlay (`overlay/<jurisdiction>/<specialty>/`). Specialty packs contain no country words; jurisdiction and case-type packs contain no specialty words or codes. Enforced by pack lint.
2. **Inheritance:** `extends:` within a layer (e.g. compensation-scheme case types extend a base `ime` pack).
3. **Merge rules:** dictionaries by key, later wins; registries unioned by `key` with `enabled: false` to remove; text files replaced whole; placeholders (`{clinician}`, `{instructing_party}`, `{subject}`) instead of cross-layer words.
4. **Neutral names everywhere in code:**
   - `instructing_party_*` replaces solicitor;
   - `party_role` replaces side;
   - `court_track` is optional;
   - `sent_to_clinician` and `actions_requested_of_clinician` replace the GP-named fields;
   - `keep_default` retention;
   - `primary_care_record` and `primary_care_visits` keys;
   - professions plus registration bodies by profession;
   - UTC storage with jurisdiction and expert timezones;
   - calendar and holiday-aware deadlines.
5. **Rule types in code, instances in packs** for "Things to look at".
6. **AI prompts and output schemas are built from packs** (`pack.prompt_context()`, registries); templates contain no country, specialty or case-type words.
7. **Fixture packs** (`packs/_fixtures/`: `zz-test`, `zz-case`, `zz-specialty`) make core tests run under a deliberately different configuration. Anything hard-coded fails.

**Consequences.**
- Foundation gains the overlay merge, placeholder resolution, calendars and holidays, timezone handling, pack lint and the fixture-pack test run (about 2 extra days).
- Every slice's acceptance tests must pass under the fixture packs as well as the launch packs.
- Adding a country or specialty is now: jurisdiction or specialty pack + overlays for the combinations sold + eval bundles. No slice code.
