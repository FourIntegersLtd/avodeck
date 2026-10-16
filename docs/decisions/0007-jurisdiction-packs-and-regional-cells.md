# 0007 Jurisdiction packs and regional cells

Date: 2026-10-15 · Status: accepted

**Decision.** Avodeck launches for England and Wales but no code assumes it.
- **Jurisdiction packs** (`packs/jurisdiction/<code>/`) hold everything legal or clinical that varies: court rules, declarations, deadlines, meeting types, legal tests, guidelines, vocabulary, date order, identifier formats, record-system hints, default checks and retention. Slices read them through `core/packs.py`. Every case has a `jurisdiction`; served reports store the pack version.
- **Regional cells:** one identical deployment per region (`REGION=uk` first), each with its own database, storage, AI endpoints and allow-list. No patient data crosses regions. A tiny global layer holds only marketing and the email-to-region directory.
- **Neutral names:** `written_questions`, `expert_meetings`, `court_bundles`, compliance checklist, `registration_body`, `currency`, `/jurisdictions/{code}`.
- **i18n:** every UI string uses a key; en-GB first.

**Why.** Australia, Canada and New Zealand have different expert rules, declarations, record systems, date conventions and privacy laws, and each expects health data to stay in-country. Building the seams now costs about 1–2 weeks; retrofitting later would touch every slice.

**Consequences.** Root rule 5 becomes "processing stays in the home region" and rule 11 forbids hard-coded country rules. A new country needs a pack (3–6 weeks with a local adviser), a cell (1–2 weeks), local evaluation bundles and local compliance.
