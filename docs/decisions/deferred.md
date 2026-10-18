# Deferred decisions

Things we chose not to build yet, and what would make us build them. Review at every phase gate (architecture 10.14). When a trigger fires, write an ADR and a slice brief.

| Deferred | Why not now | Trigger to build |
|---|---|---|
| SSO / SAML for organisations | Few team customers at launch | First team customer with more than 10 seats asks for it |
| Machine translation of foreign-language pages | Court needs certified translations; risk of mistranslation | A jurisdiction pack in a bilingual country (e.g. Quebec) or repeated requests from experts |
| Redaction tools | The bundle is evidence and kept as received | An instructing party requests redacted extracts as standard |
| Gmail/Outlook sending | `mailto:` is enough for the MVP | Experts report more than 5 minutes a case on emails |
| Cross-cell organisations | Data must stay in one cell | An organisation operating in two countries signs up |
| Exhibits analysis (audio, video, DICOM) | Out of intended purpose | A specialty pack needs it and the intended-purpose review allows it |
| Seat-based billing beyond Stripe quantity | Simple per-seat subscription suffices | Agencies ask for pooled credits or per-case billing |
| Real-time co-editing of opinions | Opinions are personal to the named expert; ETag prevents loss | A joint-report workflow needs simultaneous editing |
| Automatic reprocessing with new models | Must never change verified work silently | Never automatically; opt-in only (10.3) |
| Slice J (examination and prognosis) | Not needed for breach of duty | First causation, condition-and-prognosis or IME pack |
| Offline editing (beyond witness-box search) | Complexity and conflict risk | Experts working regularly without connection |
| IAM Roles Anywhere for App Platform → AWS | Scoped, rotated access keys are simpler to run alone | Before the first paying customer, or if a security review asks for short-lived credentials |
| T-10 second reading of a typed-out page (slice B) | The AI kit sends text only, and identifier masking on page images (M9) is not built | Foundation adds image input to `core/ai/kit.py` with M9 masking |
| Marking a page as a copy from screen 10 (`duplicate_of` on `PATCH …/pages/{page_no}`) | Slice A's copy review (`PATCH …/pages/{page_id}/duplicate`) already does it with history | Experts ask to mark copies while typing pages out |
| Embedding pre-ranking for unused facts (T-14b) | The cell has no in-region embedder yet; the model ranks the pack-ordered candidates directly | A case has more unused citable facts per question than the model call can take, or the in-region embedder lands (stage 2) |
