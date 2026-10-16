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

### notification_preferences
| Field | In plain words |
|---|---|
| person_id / organisation_id | Whose preferences, for which organisation's cases |
| channel | In the app, by email, or both |
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
| on_hold | Paused (e.g. court stay); reminders stop |
| retention_policy / delete_after | How long to keep the case after it ends, and the resulting deletion date |

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
| name / firm / email | Who they are and where to send things |
| is_joint | Whether the expert is jointly instructed (both sides) |
| effective_from / effective_to | When this party took over or stopped (e.g. a change of firm) |

### case_links
| Field | In plain words |
|---|---|
| linked_case_id | The other case |
| kind | How they relate: related claim, inquest, previous instruction, replaces |

### instruction_versions
| Field | In plain words |
|---|---|
| kind | Original letter, supplementary letter, or oral instructions |
| document_id | The letter itself (if written) |
| received_on | When the instructions were received |
| text | The typed or dictated wording of oral instructions |

### conflict_answers
| Field | In plain words |
|---|---|
| question_key | Which conflict question (e.g. "have you treated this person?") |
| answer / detail | Yes or no, and any explanation |
| answered_at | When answered (conflicts can be declared later too) |

### questions
| Field | In plain words |
|---|---|
| instruction_version_id | Which set of instructions asked it |
| number | Its number in the letter |
| text_verbatim | The exact words of the question |
| source_word_ids[] | Where those words are in the letter, as proof |
| para_ref | The paragraph of the letter |
| kind | Type of question (e.g. breach, causation) from the pack |
| outside_expertise | Flag that it may be outside the expert's field (they decide) |
| status / confirmed_text | Whether the expert has confirmed it, and any tidied wording (the original is kept) |
| version | Goes up each time the question changes |

### assumptions
| Field | In plain words |
|---|---|
| text_verbatim / source_word_ids[] | The exact words the expert is told to assume, and where they are |
| status | Confirmed or not |

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

### exhibits
| Field | In plain words |
|---|---|
| media_type | Audio, video, medical image, etc. |
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
| relevance | The expert's judgement: relevant or background |
| language | The page's language, if not the pack's |
| transcribed_by_expert_text | The expert's own typing of a page the computer couldn't read |

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
| available_at / availability_basis | When the clinician could first have seen it, and how we know |
| status | Suggested, checked by the expert, not right, or changed |
| version | Goes up when the fact is changed, so citations know |
| model_task / model_id / prompt_version | Which AI task, model and instructions suggested it |

### fact_verifications
| Field | In plain words |
|---|---|
| person_id | Who checked it |
| action | Correct, not right, or changed |
| old_value / new_value / reason | What it was, what it became, and why |

### page_reads
| Field | In plain words |
|---|---|
| page_id / person_id | Which page was read, by whom |
| read_at / dwell_seconds | When, and how long they spent on it |
| prev_hash / hash | Links to the previous entry so any tampering is obvious |

### fact_flags
| Field | In plain words |
|---|---|
| flagged_for | Why it was flagged (to consider under points against) |

### missing_items
| Field | In plain words |
|---|---|
| description | The document that seems to be missing (e.g. colonoscopy report) |
| citing_page_id | The page that mentions it |
| status | Open, requested, received, not available |

### record_gaps
| Field | In plain words |
|---|---|
| source_type | Which record source goes quiet |
| gap_start / gap_end | The quiet period |
| confirmed | Whether the expert agrees it's a real gap |

### instructing_party_requests
| Field | In plain words |
|---|---|
| items | What's being asked for |
| body_text | The email wording |
| sent_via / sent_at / message_id | How and when it was sent |
| follow_up_on | When to chase |

## Understand

### fact_question_links
| Field | In plain words |
|---|---|
| fact_id / question_id | Which fact relates to which question |
| status / reason | Suggested, confirmed or rejected, and the short reason |

### check_config
| Field | In plain words |
|---|---|
| check_key | Which "Things to look at" check |
| enabled / gentle | On or off, and whether it uses softer wording |

### findings
| Field | In plain words |
|---|---|
| check_key | Which check found it |
| evidence_fact_ids[] | The facts behind it |
| decision / reason | The expert's decision (relevant, not relevant, explained elsewhere) and why |

### availability_overrides
| Field | In plain words |
|---|---|
| available_at / unknown | The expert's corrected date, or "not known" |
| reason | Why they changed it |

### consultation_notes
| Field | In plain words |
|---|---|
| fact_id / text | Which consultation, and the expert's note |

### guideline_versions
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

### witness_claims
| Field | In plain words |
|---|---|
| document_id | Which witness statement |
| text_verbatim / word_ids[] | The exact statement and where it is |
| verdict / fact_ids[] | Whether the records support or contradict it, and which facts |
| status | Whether the expert has confirmed it |

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
| version_no / body / citation_refs | Each saved version of the text and its references |
| saved_at / saved_by | When and by whom |

### lint_issues
| Field | In plain words |
|---|---|
| sentence_index / type / words | Which sentence, what kind of warning, which words |
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
| responses | The colleague's answers |

## Money

### time_entries
| Field | In plain words |
|---|---|
| person_id / stage | Who worked, on which part |
| started_at / ended_at / seconds | When and how long |
| adjusted_by_expert | Whether the expert corrected it |

### invoices
| Field | In plain words |
|---|---|
| billing_profile_id | Who the invoice is from |
| number | Invoice number (never skips or repeats) |
| lines / total / currency / tax | What was charged, the total, currency and tax |
| status | Draft, sent, part paid, paid, written off |
| pdf_path / sent_at | The invoice file and when it was sent |

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

### opposing_claims
| Field | In plain words |
|---|---|
| text_verbatim / word_ids[] | The other expert's statement and where it is in their report |
| cited_page_ref / cited_value | The page and value they quote |
| status | Matches, wrong page, wrong value, not on page, contradicted |
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

### trial_packs
| Field | In plain words |
|---|---|
| hearing_id | Which hearing it's for |
| sections / pdf_path / sha256 / built_at | What's in it, the file, fingerprint and build date |
| stale | New material has arrived since it was built |

### offline_packages
| Field | In plain words |
|---|---|
| hearing_id / device_label | Which hearing and which device |
| format_version | The package format, so older apps can refuse newer ones |
| built_at / expires_at / revoked_at | When made, when it stops opening, and if it was cancelled |

## Close

### legal_holds
| Field | In plain words |
|---|---|
| reason / set_by | Why deletion is paused (e.g. appeal) and who paused it |
| until / released_at | Until when, and when it was lifted |

### deletion_certificates
| Field | In plain words |
|---|---|
| deleted_at / counts / hashes | When the case was deleted, how many items, and their fingerprints |
| log_chain_heads | The final fingerprints of the reading and checking logs, proving they existed |
| backup_expiry_on | The date the last backup copy disappears |
| pdf_path / sent_to | The certificate file and who received it |

### deletion_ledger
| Field | In plain words |
|---|---|
| case_id / deleted_at | A deleted case, replayed after any backup restore so it stays deleted |

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
| code_version / error_code | Which version of Avodeck ran it; what went wrong if it failed (a short code, never text) |

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
| expires_at / completed_at | When an unfinished upload is given up (7 days) and when it finished |

### notifications
| Field | In plain words |
|---|---|
| person_id / organisation_id / case_id | Who it's for, about which organisation and case |
| kind | What happened (e.g. bundle ready) |
| read_at / emailed_at | When seen and when emailed |

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
| fingerprint | A checksum of the request, so the same key with a different request is refused |
| status_code / response_body / response_headers | The first answer, returned again for a repeat; no patient text beyond what that answer held |
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
