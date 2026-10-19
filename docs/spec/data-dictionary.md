# Data dictionary (plain English)

Every table and field, explained in everyday words. Tables appear in the same order as `docs/architecture.md` section 4. Fields every table has (`id`, `created_at`, `case_id` on case tables) are explained once here and not repeated:

- **id**: a unique reference number the system gives each row. Other tables point to a row using it.
- **case_id**: which case this row belongs to. Used to keep each expert's cases private and to delete everything when a case ends.
- **created_at / at**: when the row was made (always stored in UTC and shown in the person's own time zone).

A field ending in `_id` points to a row in another table (e.g. `page_id` → a page). A field ending in `[]` holds a list. "From the pack" means the allowed values come from the case's packs, not from code.

## People

### people (anyone who can sign in)
| Field | In plain words |
|---|---|
| full_name | The person's name as shown in the app |
| email | Their sign-in email (unique within a country's system) |
| locale | Their language and spelling (e.g. British English) |
| timezone | Their local time zone, used to show dates and deadlines |
| home_region | Which country's system they belong to (UK, Australia, Canada) |
| records_custodian | Someone they nominate to look after their served cases if they die or can't work |
| closed_at | When they closed their account (empty if active) |
| email_bounced_at | When an email to them bounced; no more emails until the address changes |

### experts (the professional side of a person who gives evidence)
| Field | In plain words |
|---|---|
| person_id | Which person this expert profile belongs to |
| profession | Their profession (doctor, nurse, physiotherapist…) from the packs |
| specialties[] | Their fields of practice (e.g. general practice); a person can have several |
| registration_body | Their regulator (e.g. GMC), offered by the country pack for their profession |
| registration_number | Their registration number with that regulator |
| hourly_rate / currency | What they charge per hour and in which currency |
| default_retention | How long they normally keep a case after it ends |
| default_jurisdiction | The country/legal system their new cases usually use |

### expert_roles
| Field | In plain words |
|---|---|
| expert_id | Which expert held this role |
| specialty | The field the role was in |
| role_title | The job title (e.g. "GP partner") |
| setting | Where (e.g. in-hours practice, out of hours), from the specialty pack |
| period | From and to dates |

### expert_training
| Field | In plain words |
|---|---|
| title / year | The course or qualification and the year |

### report_templates
| Field | In plain words |
|---|---|
| owner_type / owner_id | Whether the template belongs to a person or an organisation, and which |
| storage_path | Where the Word file is kept |
| sha256 | A fingerprint of the file, proving it hasn't changed |
| style_map / heading_map | Which styles and headings in the file match which report sections |
| version | Goes up each time the template is replaced |

### declarations
| Field | In plain words |
|---|---|
| expert_id | Whose declaration |
| jurisdiction | Which legal system it's for |
| kind | Which declaration (e.g. expert's declaration) |
| text | Any wording the expert adds (court wording itself comes from the pack) |
| locked_at | When it became fixed (after their first served report) |

### signatures
| Field | In plain words |
|---|---|
| storage_path | Where the signature image is kept |
| method | Drawn on screen or photographed |

### sessions
| Field | In plain words |
|---|---|
| person_id | Whose sign-in |
| token_hash | A scrambled copy of the random token in the browser's cookie (the token itself is never stored) |
| csrf_hash | A scrambled copy of the second token the web app sends with every change, so another site cannot act for the person |
| device_label | Which device (e.g. "Chrome on Windows laptop") |
| aal | `aal1` after the password, `aal2` once the authenticator code is entered |
| mfa_verified_at | When the authenticator code was last entered (risky actions need it within 10 minutes) |
| auth_access_token, auth_refresh_token | Supabase Auth's own tokens, kept here so they never reach the browser |
| last_seen_at | When it was last used (30 minutes without use signs it out) |
| revoked_at | When it was signed out |
| revoked_reason | Why: signed out, signed out everywhere, idle, too old (12 hours), password reset |

### mfa_recovery_codes
| Field | In plain words |
|---|---|
| code_hash | A scrambled copy of a one-time backup code (the real code is only shown once) |
| used_at | When the code was used (each works once) |

### mfa_attempts
| Field | In plain words |
|---|---|
| failures / window_started_at | How many wrong authenticator codes in the current 15 minutes (5 lock MFA until the window ends) |
| last_code_hash / last_code_at | A scrambled copy of the last code accepted and when, so the same code cannot be used twice |

### email_changes
| Field | In plain words |
|---|---|
| new_email | The address the person wants to sign in with |
| old_token_hash, new_token_hash | Scrambled copies of the links sent to the old and the new address |
| old_confirmed_at, new_confirmed_at | When each link was opened; the email changes once both are |
| completed_at | When the sign-in email changed |
| cancelled_at | When a newer request replaced this one |
| expires_at | Links stop working a day after the request |

## Organisations

### organisations
| Field | In plain words |
|---|---|
| name | The organisation's name (a person's own practice or a team) |
| kind | Personal (one person) or team |
| region | Which country's system it lives in |
| billing_profile_id | Which billing details its invoices use |
| policy | Its rules for members' cases (e.g. must use our template, keep records at least X years) |
| closed_at | When it was closed |

### memberships
| Field | In plain words |
|---|---|
| organisation_id / person_id | Which person belongs to which organisation |
| role | What they can do there: owner, admin, expert, assistant, viewer (see permissions) |
| started_at / ended_at | When they joined and left (history is kept) |

### invites
| Field | In plain words |
|---|---|
| email | Who was invited |
| role | The role they'll have when they accept |
| invited_by | Who sent it |
| expires_at / accepted_at | When it stops working; when it was accepted |

### billing_profiles
| Field | In plain words |
|---|---|
| owner_type / owner_id | Whether these are a person's or an organisation's billing details |
| legal_name / address | The name and address printed on invoices |
| tax_number | VAT/GST number if registered |
| currency | Invoice currency |
| next_invoice_no | The next invoice number, so numbers never skip or repeat |
| bank_account_name / bank_sort_code / bank_account_number / bank_iban / bank_bic | Optional bank details printed in the invoice's "How to pay" box; never put in an email |

### notification_preferences
| Field | In plain words |
|---|---|
| person_id / organisation_id | Whose preferences, for which organisation's cases |
| channel | In the app, by email, or both |
| enabled | Whether messages go this way at all |
| digest | Whether to bundle emails into one a day |
| muted_kinds[] | Types of message they don't want |

### support_access_grants
| Field | In plain words |
|---|---|
| granted_by / granted_to | The expert giving permission and the support person receiving it |
| reason | Why support needs to look |
| expires_at / revoked_at | When the permission ends automatically or was cancelled |

## Case

### cases
| Field | In plain words |
|---|---|
| organisation_id | Which organisation owns the case (and pays for it) |
| named_expert_id | The expert whose evidence this is; only they sign and verify |
| jurisdiction / case_type / specialty | The three pack codes that set the case's rules and words |
| expert_role | What the expert was asked to do (e.g. breach of duty), from the pack |
| pack_versions | Which version of each pack the case uses, frozen for served reports |
| label | A short, non-identifying name for the case (e.g. "Mrs A") |
| subject_status | Whether the person the records are about is living, deceased, or a child |
| party_role | Which side instructed the expert (wording from the pack) |
| court_track | The court track, where the country has them (optional) |
| due_date | When the report is due |
| period_start / period_end | The period of care the questions are about |
| stage | Where the case is in the workflow (prepare, read, understand, write, court) |
| status | Its state: draft, processing, active, served, settled, withdrawn, archived, deleted |
| on_hold | Paused (e.g. court stay); reminders stop and processing waits until it is cleared |
| retention_policy / delete_after | How long to keep the case after it ends (a short code), and the resulting deletion date |
| settled_at | When the case was last settled; set by the system, kept when archived, cleared when reopened. The retention clock runs from it |
| closed_at | When the case was last settled or withdrawn; set by the system, kept when archived or deleted, cleared when reopened. A withdrawn case's retention clock runs from it |

### case_members
| Field | In plain words |
|---|---|
| person_id | Who has been added to the case |
| case_role | What they do on it: assistant, supervisor, co-expert or viewer |
| added_by / added_at / removed_at | Who added them and when; when removed |

### case_parties
| Field | In plain words |
|---|---|
| role_key | Their side in the claim (from the pack, e.g. other side) |
| name | The party's name (e.g. a hospital trust) |
| representative | Their lawyer or representative, if known |

### case_instructing_parties
| Field | In plain words |
|---|---|
| type_key | What sort of instructing party (solicitor, insurer, coroner…) from the pack |
| name / firm / email | Who they are and where to send things (plain text, not encrypted under the case key; see the slice C brief) |
| reference | Their own reference for the case, if given |
| effective_from / effective_to | When this party took over or stopped (e.g. a change of firm) |
| replaces_id | The earlier row this one took over from, after a change of firm |

A joint expert is one with two or more current instructing parties; no separate flag is stored.

### case_links
| Field | In plain words |
|---|---|
| linked_case_id | The other case |
| kind | How they relate: related claim, inquest, previous instruction, replaces |

### instruction_versions
| Field | In plain words |
|---|---|
| version_no | 1 for the first instructions, then 2, 3… in the order they arrived |
| kind | Original letter, supplementary letter, or oral instructions |
| document_id | The letter itself (if written) |
| received_on | When the instructions were received |
| locked_at / locked_by | When and by whom the questions in this version were locked; never undone |

### case_instructions
| Field | In plain words |
|---|---|
| instruction_version_id | The oral instruction version these words belong to |
| text | The words of the oral instructions, encrypted under the case key |
| source | Typed, or dictated (from a saved transcript) |
| transcript_id | The dictation they came from, if dictated |
| received_on | The date the instructions were given |

### case_subject_keys
| Field | In plain words |
|---|---|
| key_hash | A keyed fingerprint of the subject's identifier, used only to warn about duplicate cases; the identifier itself is never kept |

### conflict_answers
| Field | In plain words |
|---|---|
| question_key | Which conflict question (e.g. "have you treated this person?"), from the jurisdiction pack |
| answer / detail | Yes or no, and the expert's explanation (kept encrypted; only with a yes, and required where the pack says so) |
| answered_by | Who answered (the named expert) |
| version / created_at / updated_at | Goes up on each change; when first and last answered (conflicts can be declared later too) |

### conflict_answer_history
| Field | In plain words |
|---|---|
| question_key / answer / detail | One answer exactly as it was given or changed (the detail stays encrypted) |
| answered_by / created_at / recorded_at | Who gave it and when (`recorded_at` is the exact moment, for order); the answers behind a declaration are the latest ones before it |

### conflict_declarations
| Field | In plain words |
|---|---|
| outcome | No conflicts, or conflicts disclosed |
| declared_by / created_at | Who declared and when; the latest one is the current declaration |

### case_withdrawals
| Field | In plain words |
|---|---|
| reason | Why the expert withdrew (kept encrypted) |
| withdrawn_by / created_at | Who withdrew and when |

### questions
| Field | In plain words |
|---|---|
| instruction_version_id | Which set of instructions asked it |
| number | Its number in the letter |
| text_verbatim | The exact words of the question |
| short_title | A label of at most 40 characters for tabs and lists: the model's label, or the first clause of the question when it gave none. A label only; never replaces the question |
| source_word_ids[] | Where those words are in the letter, as proof |
| para_ref | The paragraph of the letter |
| kind | Type of question (e.g. breach, causation) from the pack |
| outside_expertise | Flag that it may be outside the expert's field (they decide) |
| answer_parts[] | The parts of the question the expert answers (e.g. timing only); empty means all of it. Locked once the question is confirmed |
| outside_expertise_source | Who says it is outside the expert's field: `pack` or `expert`; empty when it is not. Locked once confirmed |
| status / confirmed_text | Whether the expert has confirmed it, and any tidied wording (the original is kept) |
| version | Goes up each time the question changes |

### assumptions
| Field | In plain words |
|---|---|
| number / para_ref | Its order and the paragraph of the letter |
| text_verbatim / source_word_ids[] | The exact words the expert is told to assume, and where they are (plain text, not encrypted under the case key; see the slice C brief) |
| status / confirmed_text | Suggested, confirmed or edited, and any tidied wording (the original is kept) |

### instruction_extractions
| Field | In plain words |
|---|---|
| instruction_version_id / job_id | Which letter was read, by which job |
| questions / assumptions / rejected | How many were kept, and how many suggestions were refused because the letter did not say them |
| reopened | Whether this letter reopened a confirmed set of questions |
| model_task / prompt_version | Which AI task and prompt version read it |

### prefill_suggestions
| Field | In plain words |
|---|---|
| field | Which case detail: instructing party's name or type, the side they act for, due date, court track |
| value | The suggestion (kept encrypted): words from the letter, a pack key, or a date |
| word_ids[] | Where in the letter it comes from, as proof |
| status / decided_by / decided_at | Waiting, accepted or dismissed, by whom and when (decided once) |
| model_task / prompt_version | Which AI task and prompt version suggested it |

### prefill_runs
| Field | In plain words |
|---|---|
| job_id | The job that read the letter |
| kept / rejected | How many suggestions were kept, and how many refused |

## Bundle

### documents
| Field | In plain words |
|---|---|
| kind | What it is: records, instructions, witness statement, written questions, agenda, other expert's report, court bundle, other |
| filename / storage_path | The file's name and where it's kept (encrypted) |
| sha256 | Fingerprint proving the file is exactly as received |
| pages | How many pages it has |
| received_on / obtained_how | When it arrived and from whom/how |
| bundle_version | Which delivery of records it came in (v1, v2…) |
| unreadable_reason | Why we couldn't open it: password-protected (`locked`) or `damaged`. Empty for every file that opened. A file with a reason has no pages; the instructing party is asked for a readable copy |

### document_scans
| Field | In plain words |
|---|---|
| clean / signature | Whether the safety scan passed, and what it found if not |
| media_type | What kind of file it really is (PDF, email, image, Word, zip, audio…), read from its contents |
| scanned_at / split_at | When it was scanned and when its pages were numbered |

### document_parts
| Field | In plain words |
|---|---|
| parent_document_id | The email or zip file it came inside |
| document_id | The part itself, kept as a document of its own |
| part_no | Its place in the parent (attachments in order, zip files by name) |
| media_type | What kind of file the part is |

### exhibits
| Field | In plain words |
|---|---|
| document_id | The file it was received as (or found inside) |
| media_type | Audio, video, medical image, etc. |
| size | How big the file is |
| storage_path / sha256 | Where it's kept and its fingerprint |
| note | The expert's description (Avodeck can't read these) |

### pages
| Field | In plain words |
|---|---|
| document_id | Which file the page came from |
| bundle_page_no | Its permanent page number (never changes) |
| source_type | Where the page is from (GP record, hospital, results…) from the pack |
| image_path | Where the picture of the page is kept |
| text_source | Whether its words came from the file itself or from OCR |
| ocr_confidence | How sure the computer is that it read the page correctly |
| handwriting | Whether it's handwritten |
| rotation | How it has been turned for reading |
| duplicate_of_page_id | If it's a copy, which page it copies |
| superseded_by_page_id | If a clearer copy arrived, which page replaces it |
| quarantined | Marked as another person's record: hidden, and when, why and by whom |
| unreadable | The computer couldn't read it; it's in "Pages we couldn't read" |
| needs_clearer_copy | The expert asked for a clearer copy; the page goes on the next request to the instructing party |
| relevance | The expert's judgement: relevant, background, or nothing relevant on the page |
| language | The page's language, if not the pack's |
| transcribed_by_expert_text | The expert's own typing of a page the computer couldn't read, sealed with the case key (never stored as plain text) |

### page_analysis
| Field | In plain words |
|---|---|
| stage | How far the worker has got with the page |
| word_count / typed_words | How many words were read, and how many were typed rather than handwritten |
| phash / minhash | Fingerprints of the picture and the words, used to find duplicates |
| printed_dates | Dates printed on the page (used to find gaps) |
| extraction | What was read from the page, sealed with the case key |
| availability_evidence | When the page was received, filed, seen or sent to the clinician: each with its words on the page and the date read by code in the pack's date order (empty when the printed date could be read two ways) |
| needs_human / reason | Whether a person must check the page, with a short reason |
| model_id | Which model read the page |
| duplicate_status / duplicate_by / duplicate_at / duplicate_version | Whether it was set aside as a duplicate, who decided and when (If-Match) |
| references_checked_at | When its mentions of other documents were checked |

### page_embeddings
| Field | In plain words |
|---|---|
| page_id | The page the vector describes (one per page) |
| model | Which embedder made it; a page made by an older one is made again |
| embedding | The search vector, made from the page's words with names and identifiers taken out |
| created_at / updated_at | When it was first made and last remade |

### page_quarantines
| Field | In plain words |
|---|---|
| page_id | The page that may belong to someone else |
| status | Suggested, confirmed, dismissed, undone, or deleted (purged) |
| reason | A short label (e.g. not this person) |
| suggested_by | Who suggested it (empty when the worker did) |
| confirmed_by / confirmed_at | The named expert who confirmed it, and when |
| closed_by / closed_at | Who dismissed or undid it, and when |
| purged_at | When the page's image and text were deleted (the number stays) |
| prior_facts | Which suggestions were set aside, so an undo restores them |
| version | Changes each time it is changed (If-Match) |

### page_replacements
| Field | In plain words |
|---|---|
| page_id | The page a clearer copy replaces |
| document_id | The later records holding the clearer copy |
| new_page_id / linked_at | The new page that supersedes it, and when the link was made |
| requested_by | Who said which page it replaces |

### ocr_words
| Field | In plain words |
|---|---|
| page_id / word_index | Which page, and which word in reading order |
| text | The word |
| polygon | Where it sits on the page (used to highlight it) |
| confidence | How sure the computer is about the word |
| source | Whether it came from OCR, the file's own text, or the expert's transcription |

### facts
| Field | In plain words |
|---|---|
| page_id | The page it was found on |
| kind | What sort of thing it is (consultation, result, prescription…) from the pack |
| fields | The details (e.g. test name, units) |
| value | The key value (e.g. "9 ug/L") |
| word_ids[] | The exact words on the page it came from, the proof |
| bbox | The box drawn around it on the page |
| event_date / date_basis | When it happened, and whether that date was printed or worked out |
| available_at / availability_basis | When the clinician could first have seen it, and how we know. Set through `app.set_fact_availability()`, which keeps the fact version |
| status | Suggested, checked by the expert, not right, or changed |
| version | Goes up when the fact is changed, so citations know |
| model_task / model_id / prompt_version | Which AI task, model and instructions suggested it |

### fact_verifications
| Field | In plain words |
|---|---|
| person_id | Who checked it; empty only for `remapped` rows, which the reading job writes (0207) |
| seq | Its place in the case's list of checks |
| action | Correct, not right, changed, moved to a clearer copy, or `remapped`: the page was read again (turned or typed out) and the fact was moved onto the same words in the new reading (0207) |
| old_value / new_value / reason | What it was, what it became, and why |
| old_fields / new_fields | The details before and after a change |
| old_word_ids / new_word_ids | The words it pointed to before and after a change |
| old_page_id / new_page_id | The page it was on before and after a move to a clearer copy |
| at | When the check was made |
| prev_hash / hash | Links to the previous check so any tampering is obvious |

### page_reads
| Field | In plain words |
|---|---|
| page_id / person_id | Which page was read, by whom |
| seq | Its place in the case's list of reads |
| at / dwell_seconds | When, and how long they spent on it |
| counts_for_citing | Whether it was the named expert's read (only those let a fact be cited) |
| prev_hash / hash | Links to the previous entry so any tampering is obvious |

### fact_flags
| Field | In plain words |
|---|---|
| fact_id / person_id | Which fact, flagged by whom; no person for `recheck` flags, which the reading job writes (0205) |
| flagged_for | Why it was flagged: `points` (to consider under points against), or `recheck` ("Please check": its page was read again and its words were not found; the flag goes when the expert checks the fact again) (0205) |
| at | When it was flagged |

### missing_items
| Field | In plain words |
|---|---|
| description | The document that seems to be missing (e.g. colonoscopy report), only in words from the page; sealed |
| citing_page_id | The page that mentions it |
| reference_key | Which mention on that page (r1, r2, ...); each mention is listed once |
| word_ids | The words on the page that mention it |
| reference_date | The date printed with the mention, if any |
| confidence | How sure the model was that no page holds it |
| status | Open, requested, received, not available |
| reason | Why it is not available (required for not available); sealed |
| received_page_id | The page added later that holds it (required for received) |
| updated_by | Who last changed it |
| version | Changes each time it is changed (If-Match) |

### record_gaps
| Field | In plain words |
|---|---|
| source_type | Which record source goes quiet |
| fact_kind | Which kind of entry stops (e.g. the list of consultations) |
| gap_start / gap_end | The quiet period: from the month after the last entry to the end of the source's last month |
| last_page_id | The page with the last entry before the gap |
| confirmed | Whether the expert agrees it's a real gap (empty until reviewed; reviewed gaps are not recalculated) |
| confirmed_by / confirmed_at | Who reviewed it and when |
| version | Changes each time it is changed (If-Match) |

### instructing_party_requests
| Field | In plain words |
|---|---|
| items | What's being asked for |
| body_text | The email wording |
| sent_via / sent_at / message_id | How and when it was sent |
| follow_up_on | When to chase |
| quarantined_page_ids | Pages of someone else this request tells the instructing party about (their image and text are then deleted) |

### case_processing
| Field | In plain words |
|---|---|
| cancel_requested_at / cancel_requested_by | Who asked to stop reading the bundle, and when (it stops at the next page) |
| reprocess_opted_at / reprocess_opted_by | Who chose to have the pages read again with a newer model, and when (verified facts never change) |

## Understand

### fact_question_links
| Field | In plain words |
|---|---|
| fact_id / question_id | Which fact relates to which question (the question is cleared, and the link turns stale, if the question is removed) |
| status / reason | Suggested, confirmed, rejected or stale, and the short reason |

### check_config
| Field | In plain words |
|---|---|
| check_key | Which "Things to look at" check |
| enabled | On or off (a check the pack starts off stays off until switched on) |
| window_days | The expert's own number of days for the check, if they changed it |
| label | The name the expert gave a check they added themselves |
| custom | For a check the expert added: which entry it starts from, what should follow, and within how many days |

### findings
| Field | In plain words |
|---|---|
| check_key | Which check found it |
| evidence_fact_ids[] | The facts behind it |
| decision / reason | The expert's decision (relevant, not relevant, explained elsewhere) and why |
| status | Open, or gone when a later run no longer finds it (never deleted; a decided gone finding stays listed). One finding per check and set of evidence facts |
| gentle | Whether the screen uses the softer wording |

### check_runs
| Field | In plain words |
|---|---|
| pack_version | Which version of the packs the checks came from |
| checks | Which checks ran |

### case_guideline_versions
| Field | In plain words |
|---|---|
| code / version_label | Which guideline and which version the case was shown |
| table_reviewed_at | When that entry of the guidance table was last reviewed |
| entry_hash | A fingerprint of the guidance entry as the case was shown it; a different fingerprint now shows a notice until checks are run again |

### availability_overrides
| Field | In plain words |
|---|---|
| available_at / unknown | The expert's corrected date, or "not known" |
| reason | Why they changed it |

### consultation_notes
| Field | In plain words |
|---|---|
| fact_id / text | Which consultation, and the note |
| person_id | Who wrote it: each person keeps their own note, and only they see it |

### guideline_versions
Not a database table: these fields are the rows of the overlay pack's `guidelines.yaml`, read with `pack.guidelines()` (ADR 0016).

| Field | In plain words |
|---|---|
| jurisdiction / specialty / source | Which country, field and guidance body |
| guideline_code / title | The guideline's reference and name |
| version_label | Which version |
| in_force_from / in_force_to | The dates it applied |
| url / licence | Where to read it and the terms of use |
| table_version | Which edition of our table this row belongs to |
| reviewed_at | When we last checked it |

### comments
| Field | In plain words |
|---|---|
| author_id | Who wrote it |
| target_type / target_id | What it's about (a page, fact or paragraph) |
| text / resolved_at | The comment, and when it was dealt with |

## Write

### points
| Field | In plain words |
|---|---|
| question_id / fact_id | Which fact, for which question |
| side | Supports the claim, supports the clinician, or neutral (wording from the pack) |
| status / reason | Accepted or not, and why |

### range_notes
| Field | In plain words |
|---|---|
| question_id / text | The expert's note on what other reasonable clinicians might have done |
| created_at | The day the note was first written; screen 14 shows it as "Dictated 14 Oct" |

### relevant_facts
| Field | In plain words |
|---|---|
| question_id / fact_id | Which checked fact, for which question's draft |
| why_relevant | A short label for what the fact may bear on (never wording for the draft) |
| status | Still suggested, or dismissed by the expert |
| fact_version | The fact as it was when suggested |
| job_id / model_task / prompt_version | Which run suggested it |
| decided_by / decided_at | Who dismissed it and when |
| version | A stamp that stops two windows overwriting each other |

### witness_claims
| Field | In plain words |
|---|---|
| document_id | Which witness statement |
| text_verbatim / word_ids[] | The exact statement and where it is |
| verdict / fact_ids[] | Whether the records support or contradict it, and which facts |
| claim_index | The claim's place in the list the model returned |
| status | Whether the expert has confirmed it, given their own verdict, or dismissed it |
| expert_verdict | The expert's own verdict, when they disagree with the label |
| fact_versions | The facts as they were when compared |
| job_id / model_task / prompt_version | Which run found it |
| decided_by / decided_at | Who decided it and when |
| version | A stamp that stops two windows overwriting each other |

### witness_comparisons
| Field | In plain words |
|---|---|
| document_id | Which witness statement the expert asked to compare |
| job_id | The background job doing the comparison |
| requested_by | Who asked |
| status | Waiting, done, or refused (the answer could not be checked) |
| claims | How many claims were kept |
| created_at / finished_at | When asked and when finished |

### opinions
| Field | In plain words |
|---|---|
| question_id | Which question this answers |
| body | The expert's own words |
| citation_refs | The page references inside the text |
| view | The expert's overall answer (e.g. within or below the standard), from the pack |
| provisional | Marked as not final |
| test_text | The legal test as the expert phrased it |
| updated_at / etag | When last saved; a stamp that stops two windows overwriting each other |

### opinion_versions
| Field | In plain words |
|---|---|
| version_no / body / citation_refs / test_text | Each saved version of the text, its references and the test in the expert's words |
| created_at / saved_by | When and by whom it was saved |

### lint_issues
| Field | In plain words |
|---|---|
| sentence_index / type / words | Which sentence, what kind of warning, which words |
| (shown, not stored) sentence_start / sentence_end | Where that sentence sits in the saved text, worked out when the warning is sent, so screen 15 can mark it |
| dismissed_reason | Why the expert chose to ignore it |

## Report

### report_versions
| Field | In plain words |
|---|---|
| version_no / type / display_name | Its number; draft, served, supplementary or answers; and the name shown (e.g. "Report 1") |
| status | Draft, signed and frozen (locked), served, or withdrawn |
| docx_path / pdf_path | Where the Word and PDF files are |
| sha256 | Fingerprint proving it hasn't changed |
| locked_at | When it was signed and frozen; after this nothing in the row can change except serving or withdrawing it |
| withdrawn_at / withdrawn_reason | If the expert later withdrew it, when and why (it's never deleted) |
| prev_version_id | The version it follows on from |
| (shown on a signed supplementary report) | Who signed it is the case's named expert and the day is `locked_at` in the court's time zone; neither is stored separately |

### report_snapshots
| Field | In plain words |
|---|---|
| kind | What was frozen (CV, declaration, signature, template, pack versions…) |
| content_ref / sha256 | The frozen copy and its fingerprint |

### report_paragraphs
| Field | In plain words |
|---|---|
| para_no / section / text | Each paragraph's number, section and words |
| content_control_id | Where it sits in the Word file |
| question_id | Which question it answers |

### citations
| Field | In plain words |
|---|---|
| fact_id / fact_version | Which fact, and which version of it |
| value_snapshot / quote_verbatim | The fact's value and the exact quote at the time of citing |
| page_id | The page referred to |

### report_servings
| Field | In plain words |
|---|---|
| recipient_party_id | Who it was sent to |
| served_on / sent_via | When and how |
| correction_of / reason | If this row corrects a mistaken serving record, which one and why (rows are never edited) |

### final_check_runs
| Field | In plain words |
|---|---|
| results / passed / at | Each check's result, whether all passed, and when |

### spot_checks
| Field | In plain words |
|---|---|
| reviewer_email | The colleague asked to check |
| fact_ids[] | The sample of facts sent |
| token_hash / expires_at | The scrambled link code and when it stops working |
| code_hash / code_sent_at | The scrambled emailed code and when it was sent |
| code_attempts / verified_at | Wrong codes so far (five lock the link) and when the code was first right |
| responses / responded_at | The colleague's answers (agrees or not, a short reason label) and when |

### spot_check_address_tries
| Field | In plain words |
|---|---|
| address_hash | A scrambled copy of the internet address that opened or answered a spot-check link |
| tries / window_started_at | How many tries from that address in the current 15 minutes (over 20 are refused) |

### report_settings
| Field | In plain words |
|---|---|
| citation_style | Page references or paragraph references |
| acknowledged_declaration_version | The pack declaration version the expert has read |

### report_section_texts
| Field | In plain words |
|---|---|
| section | Which report section (a pack key) |
| body | What the expert wrote, sealed |
| citation_refs | The facts it cites |
| version | Goes up on each save (for If-Match) |

### report_builds
| Field | In plain words |
|---|---|
| citation_style / template_id / template_fallback | How the draft was laid out, and whether the standard layout replaced an unusable template |
| page_count / pack_versions | Length of the PDF and the pack versions used |

## Money

### time_entries
| Field | In plain words |
|---|---|
| person_id / stage | Who worked, on which part |
| started_at / ended_at / seconds | When and how long |
| adjusted_by_expert | Whether the expert corrected it |

### time_estimates
| Field | In plain words |
|---|---|
| stage | Which part of the work (a workflow step from the pack) |
| hours / fee | The hours the expert expects, and a fixed fee if one was agreed |
| created_by / created_at | Who gave the estimate and when; a change is a new row and the newest counts |

### invoices
| Field | In plain words |
|---|---|
| billing_profile_id | Who the invoice is from |
| number | Invoice number (never skips or repeats) |
| lines / total / currency / tax | What was charged, the total, currency and tax |
| tax_rate / issuer | The tax rate and who it is from (name, address, tax number, bank details) as they were when it was drafted |
| statements | The lines the pack requires on every invoice (for example that fees do not depend on the outcome), as they were when it was drafted |
| estimate_amount | The time estimate in money when it was drafted, shown against the invoice's total; empty without an estimate |
| status | Draft, sent, part paid, paid, written off |
| paid_amount | How much has been paid: the sum of its payments |
| pdf_path / sent_at | The invoice file and when it was sent |

### invoice_payments
| Field | In plain words |
|---|---|
| invoice_id / amount | Which invoice was paid and how much (more than nothing, never more than is owed) |
| recorded_by / recorded_at | Who recorded the payment and when |

### credit_notes
| Field | In plain words |
|---|---|
| invoice_id / number / amount / reason | Which invoice it corrects, its own number, how much and why |

## Court

### court_directions
| Field | In plain words |
|---|---|
| made_on / reference / text | When the court made the order, its reference, and what it says |
| created_deadline_ids[] | The deadlines it set or changed |

### deadlines
| Field | In plain words |
|---|---|
| kind / rule_ref | What's due and which rule sets it (from the pack) |
| due_on | The date it's due (in the court's own time zone) |
| calendar | Counted in calendar days or working days |
| source | From the pack's default, a court order, or entered by hand |
| pack_version / done_at | Which pack version worked it out; when it was done |
| days_left (shown, not stored) | Whole days from today to due_on, counted in the deadline's own time zone; below zero when late |
| cancelled_at | When it was cancelled because the case was withdrawn (never cleared; no reminder goes out; the deadline can no longer change) |

### deadline_changes
| Field | In plain words |
|---|---|
| old_due_on / new_due_on / reason / source_id | How the date moved, why, and the order or note behind it |

### hearings
| Field | In plain words |
|---|---|
| type | Trial, case management hearing, inquest… (from the pack) |
| starts_on / ends_on / venue | When and where (can be several days) |
| status | Scheduled, adjourned, vacated, heard |
| replaced_by_hearing_id | The new date if it was adjourned |

### written_question_rounds
| Field | In plain words |
|---|---|
| from_party_id / received_on | Which side sent the questions, and when |
| deadline_id | When the answers are due |

### written_questions
| Field | In plain words |
|---|---|
| round_id / number / text_verbatim / word_ids[] | Which batch, its number, the exact words and where they are |
| classification | E.g. asks for clarification or goes beyond the report (from the pack) |
| paragraph_ids[] | Which report paragraphs it relates to |
| answer_text / status | The expert's answer and whether it's done |

### opposing_reports
| Field | In plain words |
|---|---|
| document_id / received_on | The other expert's report (an uploaded document) and when it arrived |
| job_id / checked_at / claims_found | The check that read it, when it finished and how many statements it found |
| positions | Each question's answer in their report beside ours, for the expert to compare (no new wording) |

### opposing_claims
| Field | In plain words |
|---|---|
| text_verbatim / word_ids[] | The other expert's statement and where it is in their report |
| cited_page_ref / cited_value | The page and value they quote |
| status | Matches, wrong page, wrong value, not on page, contradicted; shown as "read this page first" (with our words hidden) until the viewer has read our page |
| evidence_word_ids[] / expert_confirmed | The words on our page that show it, and whether our expert agrees |

### report_changes
| Field | In plain words |
|---|---|
| kind | Correction or change of opinion |
| paragraph_id / old_text / new_text / reason | What changed and why |
| dependent_paragraph_ids[] | Other paragraphs that may need changing too |
| supplementary_version_id | The supplementary report it went into |

### expert_meetings
| Field | In plain words |
|---|---|
| type | Joint statement, joint report, conclave… (from the pack) |
| discussion_on / sign_by / send_by | When the experts meet and the deadlines to sign and send |
| status | Draft, out for signature, signed, signed outside Avodeck |
| signed_document_id / signed_hash | The signed document and its fingerprint |

### expert_meeting_participants
| Field | In plain words |
|---|---|
| name / discipline / side | Each expert in the meeting, their field, and which side instructed them |
| person_id | Their Avodeck account, if they have one |

### agenda_versions
| Field | In plain words |
|---|---|
| version_no / document_id / received_on | Each version of the agenda, the file, and when it arrived |

### agenda_items
| Field | In plain words |
|---|---|
| number / text_verbatim | The agenda question |
| own_position / other_positions | Our expert's answer and the others' answers |
| agreed / reason | Whether they agree, and why not |
| served_paragraph_ids[] / deviation_status | Links to the served report, and whether the position has changed from it |

### court_bundles
| Field | In plain words |
|---|---|
| version / section_prefix | Which issue of the court bundle, and its section letter (e.g. "C") |
| superseded_by_id | The newer issue that replaced it |
| pages / received_on | Page count and when it arrived |

### concordance
| Field | In plain words |
|---|---|
| page_id / trial_page_no | Our page and its number in the court bundle |
| score / method / confirmed | How sure the match is, how it was found, and whether the expert confirmed it |

### trial_pack_settings
| Field | In plain words |
|---|---|
| sections / include_guidance | Which parts go in the trial pack, and whether the pack's guidance pages are added |
| hearing_id | Which hearing the pack is for |
| requested_job_id | The build that is running now, if any |
| version | Changes each time the choice is saved, so two edits cannot overwrite each other |

### trial_packs
| Field | In plain words |
|---|---|
| hearing_id | Which hearing it's for |
| sections / pdf_path / sha256 / built_at | What's in it, the file, fingerprint and build date |
| stale / stale_reason / stale_at | New material has arrived since it was built, why (a short key such as `hearing_adjourned`), and when it was marked |
| built_hearing_version / built_last_page_no / built_served_version_id | What it was built from: the hearing's version, the last numbered page and the newest served version; it reads as stale when any of them has moved on |

### offline_packages
| Field | In plain words |
|---|---|
| hearing_id / device_label | Which hearing and which device |
| format_version | The package format, so older apps can refuse newer ones |
| built_at / expires_at / revoked_at | When made, when it stops opening, and if it was cancelled |
| stale / stale_reason / stale_at | New material has arrived since it was made, why (a short key), and when it was marked; a stale copy never becomes current again |
| court_bundle_id / built_hearing_version / built_last_page_no / built_served_version_id | What it was made from: the court bundle whose page numbers it carries, the hearing's version, the last numbered page and the newest served version |

### readiness_replies
| Field | In plain words |
|---|---|
| question_no | Which question the reply answers |
| reply / replied_at / replied_by | The expert's own reply (sealed), when it was written and by whom; printed on the question's sheet in the pack |

### trial_notes
| Field | In plain words |
|---|---|
| text / text_sha256 | A sentence of a pasted note found in nothing served (sealed), and its fingerprint so a re-check finds the same row |
| choice / decided_at / decided_by | Tell the instructing party or leave it out of the pack, when, and by whom |

Also: `court_bundles.missing_told_at` (when the expert told the instructing party about missing pages) and `trial_packs.section_pages` (the pages each section took when built).

## Close

### legal_holds
| Field | In plain words |
|---|---|
| reason / set_by | Why deletion is paused (e.g. appeal) and who paused it |
| until / released_at / released_by | Until when, when it was lifted and by whom (expired holds are lifted by the daily run). Lifted once, at the moment and in the name of the person lifting it; never put back |

### deletion_certificates
| Field | In plain words |
|---|---|
| deleted_at / counts / hashes | When the case was deleted, how many items, and their fingerprints |
| log_chain_heads | The final fingerprints of the reading and checking logs, proving they existed |
| backup_expiry_on | The date the last backup copy disappears |
| sent_to | Who received it, as role codes (never addresses); each is recorded as its email goes, so a retried deletion never sends twice. Nothing changes once `deleted_at` is set |
| created_at | When the deletion started; `deleted_at` stays empty until it finishes |

### deletion_ledger
| Field | In plain words |
|---|---|
| case_id / deleted_at | A deleted case, replayed after any backup restore so it stays deleted |
| created_at | When the line was written |

## System

### case_keys
| Field | In plain words |
|---|---|
| wrapped_key | The case's encryption key, locked by the key service |
| key_ref | Which master key locks it (a reference, never the key) |
| created_at | When the key was made (first time anything in the case was encrypted) |
| destroyed_at | When it was destroyed at deletion (after this nothing in the case can be read) |

### jobs
| Field | In plain words |
|---|---|
| kind / status / priority | What the background work is, its state, and how urgent |
| attempts | How many times it has been tried |
| step / pct / eta_seconds | Which step it is on (a short code), progress and time left |
| step_done / step_total | Whole numbers for the current step ("page 40 of 900"); empty until the job reports them, and start again when the step changes |
| code_version / error_code | Which version of Avodeck ran it; what went wrong if it failed, or `case_on_hold` while it waits for its case to resume (a short code, never text) |
| requested_by | The person who asked for it; empty for work started by another job or the daily run. Set by the system |

### job_kind_actions
| Field | In plain words |
|---|---|
| kind / action | A kind of background job and the case action a person needs to start it |

### job_steps
| Field | In plain words |
|---|---|
| job_id / case_id | Which job, on which case |
| step | A step that finished (a short code), so a retry skips it |
| created_at | When it finished |

### scheduled_runs
| Field | In plain words |
|---|---|
| name / run_date | Which daily job, for which date; each date runs once |
| started_at | When that date's run started |

### uploads
| Field | In plain words |
|---|---|
| case_id / person_id | Which case the file is for and who is sending it |
| kind / filename / size / sha256 | What the file is (a kind from the case's packs), its name, size and checksum |
| chunk_size / chunk_count / received | The 8 MB parts it is sent in, how many, and which have arrived |
| status | `open`, `complete` or `expired` |
| document_id | The document it became when complete |
| append | Whether these are later records added to a case that already has its bundle (their pages are numbered after the last page) |
| expires_at / completed_at | When an unfinished upload is given up (7 days) and when it finished |

### notifications
| Field | In plain words |
|---|---|
| person_id / organisation_id / case_id | Who it's for, about which organisation and case |
| kind | What happened (e.g. bundle ready), as a short code the app turns into words |
| in_app | Whether it shows in the app (false when the person only wants email) |
| read_at / emailed_at | When seen and when emailed |
| held_at | Set while the message waits because its case is on hold; it is not shown or emailed until the case resumes |

### feature_flags
| Field | In plain words |
|---|---|
| key / scope / scope_id / enabled | Which feature, switched on for whom (everyone, a region, an organisation or a person) |

### audit_log
| Field | In plain words |
|---|---|
| person_id / organisation_id | Who did it and on behalf of which organisation |
| action / target | What they did and to what |
| prev_hash / hash | Links each entry to the last so tampering shows |

### idempotency_keys
| Field | In plain words |
|---|---|
| person_id / key | Whose request and the key their app sent with it |
| case_id | The case the request was about (a repeat on another case is refused; removed with the case) |
| fingerprint | A checksum of the request, so the same key with a different request is refused |
| status_code / response_body / response_headers | The first answer (body sealed with the cell key), returned again for a repeat only after the route's access and step-up checks pass |
| created_at / expires_at | When it was kept and when it is forgotten (24 hours) |

### ai_calls
| Field | In plain words |
|---|---|
| task_id / model_id / region | Which AI job, which model, and where it ran |
| prompt_version | Which version of the instructions was used |
| input_tokens / output_tokens | How much text went in and out (for cost); never the text itself |
| case_id / person_id | Which case it was for (empty for work outside a case) and who asked, if anyone |
| outcome | `ok`, or `invalid_output` when the answer did not fit its schema |

### transcripts
| Field | In plain words |
|---|---|
| context / text / duration_seconds | What the dictation was for (a short code), the final words (encrypted under the case's key), and how long |

### may_matter
| Field | In plain words |
|---|---|
| fact_id / question_id / why | A new fact that might affect an answer, and why |
| seen_at | When the expert looked at it |
