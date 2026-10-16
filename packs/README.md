# Packs

Everything that varies by country, case type or specialty lives here, never in slice code. See `docs/architecture.md` section 3 and decisions 0007, 0008 and 0011.

| Layer | Folder | Launch pack | Holds |
|---|---|---|---|
| 1. Jurisdiction | `jurisdiction/` | `gb-eaw` | Court rules, declarations, deadlines (with calendar rule), timezone, holidays, date order, currency, identifiers, retention, registration bodies by profession, party and instructing-party labels |
| 2. Case type | `case-type/` | `clin-neg-breach` | Workflow, expert roles, party roles, instructing-party types, question types, report sections, final checks |
| 3. Specialty | `specialty/` | `general-practice` | Profession, fact kinds and fields, source types, lanes, checks, neutral vocabulary. **No country words** |
| 4. Overlay | `overlay/<jurisdiction>/<specialty>/` | `gb-eaw/general-practice` | Things that depend on both: record systems, guideline bodies, local clinician title, drug monitoring, speech phrases |
| Tests only | `_fixtures/` | `zz-test`, `zz-case`, `zz-specialty` | Deliberately strange packs that prove the engine is pack-driven |

Merge order 1 → 4; later layers add or override. Registries merge by `key`; text files are replaced whole; `extends:` lets a pack inherit from another in its layer. Use `{clinician}`, `{clinicians}`, `{instructing_party}`, `{subject}` placeholders instead of words from another layer.

## Adding a pack
1. Copy an existing pack in the same layer; replace every file, with a legal or clinical adviser reviewing it.
2. Add an overlay for each jurisdiction × specialty combination you sell.
3. Add synthetic bundles and expected facts in `evals/<jurisdiction>/<case-type>/<specialty>/`.
4. Allow it in the cell config (`ALLOWED_PACKS`).
5. Bump `version` on every change. Served reports keep the versions they were built with.
6. A new case-type pack must reuse at least 70% of existing screens; otherwise it is a product decision.
7. Run `make test` (schema, lint and fixture-pack tests) and `make eval`.
