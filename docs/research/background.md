# Background research (not needed for building)

This file keeps the research, evidence and reasoning behind Avodeck's decisions: vendor comparisons, compliance analysis, accuracy studies, costs, market notes and expansion options. **Claude Code sessions do not need to read it** unless a task is about a vendor, cost, legal, compliance or market decision. The build specification is `docs/architecture.md`.

Some figures are October 2026 list prices or secondary sources; re-check before relying on them. Section numbers below are from the original research report.

---

# Avodeck: AI, API and Infrastructure Map for a UK Clinical-Negligence Records-Review Tool (October 2026, updated 15 Oct: Vercel + FastAPI + Supabase + DigitalOcean stack)

You can build Avodeck with UK-only processing. The core would be one LLM family, Claude Sonnet 5 / Opus 5.5 on Amazon Bedrock with in-region inference in London (eu-west-2), plus one OCR service, Azure AI Document Intelligence in UK South, plus one UK-hosted speech service, Azure Speech en-GB. The app itself runs on Vercel (frontend), FastAPI on DigitalOcean App Platform in London (API + worker) and Supabase in London (Postgres, Auth, Storage); there are no servers to manage and no SSH. Most of the 29 screens (roughly 70% of the listed capabilities) need no AI at all: numbering, read logs, locking, hashing, deadlines, deletion, concordance and most final checks are deterministic code. The modelled AI cost is roughly US$25–40 per 1,000-page case and under US$10 per 4,000-page trial bundle.

## TL;DR
- **Stack:** Bedrock London in-region (Claude Sonnet 5 / Opus 5.5) for extraction, checks and comparison; Azure Document Intelligence Layout (UK South) for OCR with word-level bounding boxes; Azure Speech en-GB for dictation. Hosting: Vercel for the frontend (no patient data), FastAPI on DigitalOcean App Platform London for the API and background worker, Supabase London for Postgres + pgvector, login and file storage, Resend for patient-free notification email. AWS and Azure are used only as AI API keys, not for hosting. Avoid Azure OpenAI UK South and Mistral for patient data for now. UK South GPT capacity and zero-retention approval are hard to get for a solo founder, and Mistral is EU-only, not UK.
- **Where AI is needed:** OCR, per-page event extraction, date and source classification, missing-record detection, LOI question parsing, anomaly checks that need judgement, linting, conflicting-account detection and opposing-report verification. Everything else is deterministic, including duplicate detection and trial-bundle page matching (perceptual hash + MinHash + sequence alignment) and quote verification (string matching).
- **Costs:** about US$25–40 AI cost per 1,000-page case. Total monthly cost is about US$0.65k at 10 experts, US$3k at 50 and US$11.5k at 200, with AI dominating. The MVP should be screens 01, 02, 04–10, 11 (basic), 13 (rule-based subset), 15–18b and 26. Joint-statement e-signature, trial-bundle matching, offline witness mode and opposing-report verification come later.

---

## Key Findings

1. **UK in-country LLM inference is now available but model-specific.** AWS announced on 30 September 2026 in-region support for Claude Opus 5.5 and Claude Sonnet 5 on Bedrock in London. In AWS's words, "Amazon Bedrock processes inference requests and data within the Region you call, and the processing never leaves that Region." Other Claude versions in London are only available through Geo/Global profiles. The EU Geo profile is not a UK guarantee, and Global routing has no residency constraint, so you must pin the in-region model ID. Regional endpoints carry a 10% premium over Global, which puts Sonnet 5 at about $2.20/$11 per million input/output tokens versus $2/$10.
2. **Gemini on Vertex in London is limited.** Secondary reports of Google's locations documentation (September 2026) say only Gemini 3.5 Flash has UK in-country residency and ML processing in europe-west2. It is a fallback, not a primary model.
3. **Azure OpenAI UK South is the weakest path for a solo founder.** Microsoft Q&A users report that:
   - Data Zone Standard is not offered in UK regions.
   - GPT-4o/4.1 can no longer be newly deployed in UK South (June 2026).
   - GPT-5 is not available as a UK South Standard (regional) deployment.

   Zero data retention needs "modified abuse monitoring" approval, which is gated and typically only for EA/MCA customers.
4. **NICE has no free version-history API.** NICE states: "Previous versions of NICE guidance, withdrawn guidance and information published as part of guidance development are not available through the NICE API." It also says that "If you want to use Artificial Intelligence (AI) on NICE content you must do so under licence through the NICE API." You will need a hand-curated "guideline version in force" table.
5. **Email send-as-user is easy if you only send.** Gmail's `gmail.send` is a sensitive scope, not a restricted one: it needs OAuth verification but no CASA assessment. `gmail.compose` (drafts) is restricted and triggers annual CASA. Microsoft Graph delegated `Mail.Send` ("Send mail as a user") does not require admin consent by its permission flag, but Microsoft's publisher verification documentation warns: "Beginning November 2020, if risk-based step-up consent is enabled, users can't consent to most newly registered multitenant apps that aren't publisher verified."
6. **The 20-page intermediate-track limit will rarely apply to GP breach reports.** CPR 28.14(3)(c) caps expert reports at 20 pages "excluding any necessary photographs, plans and academic or technical articles", "unless the court orders otherwise". Clinical negligence claims are excluded from the intermediate track unless breach and causation are admitted.
7. **Electronic signatures on Part 35 reports are lawful.** CPR 5.3 permits signature by mechanical means. The Law Commission's 2019 report (Law Com No 386) confirmed that an electronic signature is capable of satisfying a statutory signature requirement. PD 22 para 3.8 requires the signer's full name printed beneath the signature. A Civil Procedure Rule Committee consultation on consolidating CPR Part 5 e-signature provisions is under way.
8. **Accuracy evidence argues for "suggest, verify, cite".**
   - In a 2026 UK preprint, 27–65% of transcripts from eight speech systems contained at least one clinically significant error.
   - Asgari et al. (TORTUS), npj Digital Medicine 8:274 (13 May 2025), tested 18 configurations on 12,999 clinician-annotated note sentences and found a "1.47% hallucination rate (44% rated 'major') and a 3.45% omission rate (17% 'major')".
   - Wu et al.'s SourceCheckup (Nature Communications 16:3615, 2025; 7 LLMs, 800 questions, 58,000 statement–source pairs) found "between 50% and 90% of LLM responses are not fully supported, and sometimes contradicted, by the sources they cite. Even for GPT-4o with Web Search, approximately 30% of individual statements are unsupported, and nearly half of its responses are not fully supported."

   Every AI output must be span-anchored and human-verified, which matches your design.

---

## 1. Service key used in the tables

| Code | Service | Residency | List price (Oct 2026) |
|---|---|---|---|
| **LLM-1** | Claude Sonnet 5, Bedrock in-region | London eu-west-2 | ~$2.20/$11 per M tokens; cache reads ~10% of input; batch 50% off where supported |
| **LLM-1+** | Claude Opus 5.5, Bedrock in-region | London | Higher; hard cross-document reasoning only |
| **LLM-2** | Gemini 3.5 Flash, Vertex AI | europe-west2 | Cheaper fallback |
| **LLM-3** | Azure OpenAI UK South (Standard) | UK South, capacity-constrained | ZDR gated |
| **LLM-cheap** | Claude Haiku 5.5 (released 7 Oct 2026, $0.10/$0.50 per M up to 100K-token prompts) | London in-region **not confirmed** | Classification/routing once listed |
| **OCR-1** | Azure AI Document Intelligence Layout/Read | UK South | Layout $10, Read $1.50 per 1,000 pages |
| **OCR-2** | AWS Textract | London | Text $1.50, Layout $4, Tables $15 per 1,000 |
| **OCR-3** | Mistral OCR 4/4.1 | EU only | $4 per 1,000 ($2 batch) |
| **VLM** | Claude Sonnet 5 vision or Gemini 3.5 Flash vision | London | ~$0.01/page (modelled) |
| **STT-1** | Azure Speech en-GB (+ Custom Speech) | UK South | $1/h real-time, $0.18/h batch, $1.20/h custom |
| **STT-2** | Deepgram Nova-3 Medical (en-GB listed) | EU endpoint | Per-minute; set `mip_opt_out=true` |
| **STT-3** | AssemblyAI Universal-3.5 Pro + Medical Mode | EU endpoint | $0.36/h async |
| **STT-4** | Whisper self-hosted | DigitalOcean London (GPU needed) | Compute only |
| **EMB** | In-region embeddings (Bedrock if listed for London, else self-hosted bge/e5) + pgvector; optional cross-encoder reranker | London | Pennies per case |
| **PII** | Microsoft Presidio + UK recognisers (NHS number mod-11, postcode, DOB) + per-case name dictionary | FastAPI worker (London) | Compute only |

---

## 2. Master capability table

Format per row: **Capability** | Trigger | AI? | Model/API options | Downstream (state, storage, notifications) | Human check / failure modes | Cost | Build/Buy.

### 01 Sign in and 02 Expert profile
| Capability | Trigger | AI? | Options | Downstream | Human check | Cost | B/B |
|---|---|---|---|---|---|---|---|
| Authentication + session management | Login | No AI | Supabase Auth (London project), called only by FastAPI; session held in an httpOnly cookie | Short-lived JWT + refresh; audit row; idle timeout 30 min; device list/revoke | Lockout; anomalous-login email | ~$0 | Buy |
| Forgotten password | Link | No AI | Supabase Auth reset; email via Resend (custom SMTP) | Single-use token ≤15 min; revoke all sessions; confirmation email | No case data in email | ~$0 | Buy |
| MFA | First login | No AI | Supabase Auth TOTP MFA, called by FastAPI server-side; FastAPI enforces `aal2` | Step-up re-auth for sign, send, export, delete | Make **mandatory**; recovery codes | ~$0 | Buy |
| Name, GMC no., specialty, training, roles with date ranges | Form save | No AI | Supabase Postgres (`daterange`) | Versioned profile; feeds front matter, conflicts cross-check and expertise lint | Manual GMC register check | ~$0 | Build |
| Upload .docx template | Upload | No AI (optional LLM-1 heading→PD35 mapping) | python-docx parse styles + `numbering.xml` in the FastAPI worker; LibreOffice preview; Aspose.Words fallback | Original stored immutably; style + heading map | Expert confirms mapping; multilevel numbering is the failure point | <10 s | Build |
| Declaration (editable once) / SoT (locked) | Edit / constant | No AI | Supabase Postgres `locked_at`; hard-coded PD35 SoT with checksum | Versioned; hash-checked at final check | – | ~$0 | Build |
| Signature (draw / photo) | Canvas / upload | No AI | signature_pad; OpenCV background removal; private Supabase Storage bucket | Applied only at screen 18 after re-auth | Preview at print size | ~$0 | Build |

### 03 Today, 04 New case, 05 Conflicts
| Capability | Trigger | AI? | Options | Downstream | Human check | Cost | B/B |
|---|---|---|---|---|---|---|---|
| "3 things need you", reading-time estimates, progress rings, side-balance stats and 12-month chart | Page load | No AI | SQL scoring (deadline × blocked × unread); time = unread pages × expert's own median sec/page by page type | Read-only | Labelled estimates | ~$0 | Build |
| Upload LOI, records, other docs | Drag-drop | No AI | Chunked upload through FastAPI, streamed to Supabase Storage (London, private bucket), SHA-256, ClamAV in the worker | Case `draft→uploaded`; ingest job queued; ETA from measured pages/min via server-sent events from FastAPI | Encrypted/corrupt PDFs flagged | Upload-bound | Build |
| Solicitor, side, due date, track | Form | No AI (optional LLM-1 prefill from LOI) | Postgres | Deadlines created; track sets page-limit flag | Expert confirms prefill | <$0.02 | Build |
| 20-page tracker | Track = intermediate | No AI | Page count from rendered PDF | Counter on 16 | Default off unless breach + causation admitted | ~$0 | Build |
| Oral instructions by dictation | Mic | AI (STT) | STT-1; STT-2/3 (EU); STT-4 | Transcript stored as dated "Oral instructions" document; audio deleted; feeds instructions section (CPR 35.10(3)) | Expert edits; drug names | ~$0.02/min | Buy |
| Save and finish later | Autosave | No AI | Draft state in Supabase | Reminder on Today | – | ~$0 | Build |
| Conflict Qs → auto-insert declarations; practice-date cross-check | Answer / incident dates known | No AI | Templates; date-range overlap | Conflicts section regenerated; warning "no GP role recorded for period" | Expert reads | ~$0 | Build |

### 06 What you've been asked
| Capability | Trigger | AI? | Options | Downstream | Human check | Cost | B/B |
|---|---|---|---|---|---|---|---|
| Extract numbered questions + paragraph refs | LOI OCR done | AI | LLM-1 schema-constrained JSON with verbatim spans + char offsets; LLM-2/3 | `question` rows `suggested`; exact-substring check rejects paraphrase | Side-by-side LOI highlight; merged/split sub-questions | <$0.05; 5–15 s | Build |
| Breach/causation; outside expertise; assumptions | After extraction | AI | LLM-1 (LLM-cheap later) | Category, expertise flag vs profile, `assumption` rows with spans | Expert confirms each | <$0.04 | Build |
| Confirm/edit wording | Edit | No AI | Versioning | LOI original preserved | – | ~$0 | Build |
| "Draft a note to the solicitor" | Button | Template (+ optional LLM-1 polish) | PII strip first | Draft → send path (08b); request logged | Never auto-sent | <$0.01 | Build |

### 07 Preparing your bundle (batch pipeline)
| Capability | Trigger | AI? | Options | Downstream | Human check | Cost | B/B |
|---|---|---|---|---|---|---|---|
| Split into pages | Ingest | No AI | qpdf/pikepdf; pypdfium2 render 150–200 dpi (avoid PyMuPDF unless licensed, AGPL) | `page` rows + image hashes; thumbnails | Deskew, original kept | Seconds | Build |
| OCR incl. handwriting | Per page | AI | OCR-1 Layout (words, polygons, confidence, tables, handwriting); OCR-2; OCR-3 (EU); VLM fallback | OCR JSON to Supabase Storage; text + word boxes to Postgres; low confidence → screen 10 | Never auto-correct handwriting | $10/1,000 pages; ~2–6 min | Buy |
| Classify source | After OCR | AI + rules | EMIS/SystmOne headers, letterheads, lab patterns; then LLM-1 in the per-page call | `source_type` + confidence | Expert re-tags | Included | Build |
| Order by date | After extraction | AI + rules | LLM-1 date spans → deterministic DMY normaliser (`dateparser` DATE_ORDER=DMY); EMIS line-date regex | `event_date`, `date_basis` (printed/inferred) | "Date uncertain" badge | Included | Build |
| Near-duplicates (set aside) | After OCR | No AI | pHash (imagehash) + MinHash/LSH (datasketch) + rapidfuzz; require date/value equality | `duplicate_of`; hidden from queue, still numbered | Expert reviews set (08) | ~$0 | Build |
| Missing: referenced but absent | After extraction | AI | LLM-1 extracts outbound references ("CT report awaited") → match inventory by date/source/type | `missing_item` with citing page | Expert confirms | ~$0.5–1/case | Build |
| Record gaps (task list stops in January) | After extraction | No AI | Per-source monthly density | `gap` rows | Expert confirms relevance | ~$0 | Build |
| Email when finished | Job complete | No AI | Resend, no patient data | – | – | ~$0 | Buy |

### 08 Bundle ready and 08b Ask the solicitor
| Capability | Trigger | AI? | Options | Downstream | Human check | Cost | B/B |
|---|---|---|---|---|---|---|---|
| Permanent page numbering | Bundle lock | No AI | Immutable `bundle_page_no` + hash | Later records append (e.g. 1269+) | Never renumbered | ~$0 | Build |
| Provenance table; review duplicates; unreadable count; missing list | Form / page load | No AI | Postgres; side-by-side viewer | Feeds methodology | Expert edits/decides | ~$0 | Build |
| Email draft (missing records, clearer copies, optional extension) | "Ask the solicitor" | Template (+ optional LLM-1) | Deterministic list | Versioned draft | Expert edits | <$0.01 | Build |
| Strip patient identifiers | Before display and every LLM call | AI-assisted rules | PII (Presidio + case dictionary) | Pseudonymised text ("the patient", "[DOB]") | Removed spans highlighted; unusual names missed without dictionary | ~$0 | Build |
| "Send from my email" | Button | No AI | Graph `POST /me/sendMail` (delegated `Mail.Send`); Gmail `users.messages.send` (`gmail.send`); MVP fallback `mailto:`/.eml | Message-id stored; request log; follow-up reminder | Final text reviewed in Avodeck | ~$0 | Build |
| Later records appended | New upload | AI (new pages only) | Screen 07 pipeline | New numbers; "new since last read"; missing list re-run | Blind read applies | ~$0.03/page | Build |

### 09 Reader and 10 Pages we couldn't read
| Capability | Trigger | AI? | Options | Downstream | Human check | Cost | B/B |
|---|---|---|---|---|---|---|---|
| Viewer with zoom | Open page | No AI | Pre-rendered PNG tiles in Supabase Storage (match OCR coordinates), streamed to the browser by FastAPI | No direct storage links | – | <200 ms | Build |
| Blind read | Page open | No AI | **Server-side** gating: API returns no suggestions until `page_read` recorded | Suggestions unlocked | Enforce in API, not UI | ~$0 | Build |
| Period-in-question banner | Page open | No AI | Date compare | Banner | – | ~$0 | Build |
| Per-page structured extraction with bounding boxes | Precomputed at ingest | AI | LLM-1 JSON schema (encounter type, clinician, problems, examination, prescriptions, results); **each item cites OCR word IDs**; server unions polygons into bbox | `fact` rows `suggested` with page, word IDs, bbox, verbatim span | Items whose value isn't in cited words auto-rejected | ~$0.009/page (≈half with batch) | Build |
| Correct / Not right / Change a detail | Click | No AI | Event-sourced `fact_verification` | `verified`/`rejected`/`edited` (old value kept); feeds eval set | Only `verified` facts citable | ~$0 | Build |
| Flag into Points against | Click | No AI | Link table | Appears on 14 | – | ~$0 | Build |
| Page-read audit log, shortcuts, progress | View events | No AI | Append-only hash-chained table; criterion e.g. dwell ≥N s + whole page viewed | Exported at close | State criterion in methodology | ~$0 | Build |
| Low-confidence handwriting queue | OCR confidence < threshold or OCR/VLM disagreement | No AI | Threshold | Queue | – | ~$0 | Build |
| Expert transcription (type/dictate) | Type / mic | AI (STT optional) | STT-1/2/3/4 | Provenance "transcribed by expert"; re-extraction | Expert text authoritative | ~$0.02/min | Build+Buy |
| Relevance tag; mark as copy; ask for clearer copy | Click | No AI | Tags; `duplicate_of`; adds to 08b request | Queue updated | – | ~$0 | Build |

### 11 Timeline and 12 As the GP saw it
| Capability | Trigger | AI? | Options | Downstream | Human check | Cost | B/B |
|---|---|---|---|---|---|---|---|
| Swim lanes, filters, date zoom | Page load | No AI | SQL + vis-timeline/canvas | – | Verification colour shown | ~$0 | Build |
| Link events to questions | Drag / suggestion | AI suggestion | EMB similarity + LLM-1 rerank | `event_question` suggested/confirmed | Expert confirms | <$0.05/case | Build |
| "As the GP saw it on…" / hide later notes | Toggle | No AI | Filter on `available_at` | – | Unknown availability greyed "?" | ~$0 | Build |
| Temporal reconstruction (results filed, letters received, OOH reached practice) | Open consultation | AI + rules | LLM-1 extracts **clinical date and availability evidence** ("received", "filed by", workflow/task lines, OOH "sent to GP"); rule: visible iff `available_at ≤ consultation_at` | `available_at`, basis, confidence | **Essential** check; unknown never shown as available | Included | Build |
| Guideline version in force | Open consultation | No AI | Curated table (guideline → version → effective dates → licensed archive) | Version label shown | Expert confirms; quarterly review | Curation time | Build |
| Expert's note with dictation | Mic | AI (STT) | STT-1 | Note on consultation | Expert edits | ~$0.02/min | Buy |

### 13 Things to look at
| Check | Logic | AI? |
|---|---|---|
| Abnormal result, no action | Abnormal flag/range AND no action event within N days | Rules; LLM-1 decides if free text counts as action |
| Referral acknowledged | Referral → acknowledgement/appointment within N weeks | Rules + EMB/LLM specialty match |
| Repeat medicines monitored | Curated drug→test→interval table (e.g. methotrexate, lithium, DOACs, ACEi) | Rules |
| OOH reached GP | OOH record → later practice reference or "received" stamp | Rules + LLM match |
| Hospital letters acted on | LLM-1 extracts "GP to…" requests → rules check follow-through | LLM + rules |
| Examination recorded | Complaint → examination items ("gentle": red-flag complaints only) | Extraction-based rule |
| Safety-netting recorded | LLM classification of span | LLM (abbreviations such as "SN given" cause misses) |
| Missed appointments followed up | DNA → contact within N days | Rules |

Each finding cites evidence pages. The expert must mark Relevant / Not relevant / Explained elsewhere with a reason. Toggles and "gentle" variants are config rows; changes re-run checks and update the methodology list. Custom checks start as a no-AI rule builder (later: natural language → rule via LLM-1, previewed before enabling). Cost is ~$1–2 per case. Build.

### 14 Points against and 15 Your opinion
| Capability | Trigger | AI? | Options | Downstream | Human check | Cost | B/B |
|---|---|---|---|---|---|---|---|
| Facts for claim vs for GP | Open question | AI suggestion | LLM-1 sorts **supplied verified fact IDs** (cannot invent) | Suggested placements | Expert moves/accepts; both sides shown | ~$0.1–0.3/question | Build |
| Relevant facts not yet used | Draft saved | AI | EMB + reranker + LLM-1 filter | "Not yet used" list | Expert dismisses/uses | <$0.1 | Build |
| Dictated "what other GPs might do"; opinion dictation | Mic | AI (STT) | STT-1 with Custom Speech phrase list; STT-2/3 | Expert's own text | Product never generates opinion | ~$0.02/min | Buy |
| Conflicting accounts | Witness statement uploaded | AI | LLM-1: atomic claims with spans → retrieve record facts → supports/contradicts/not addressed | `conflict` rows with two verbatim spans | Expert confirms | ~$0.5–1/statement | Build |
| Fact chips | Click | No AI | Date + verbatim quote + `[p. 234]` as citation object | Linked to fact ID | Only verified facts | ~$0 | Build |
| Guidance in force; "your view"; Bolam/Bolitho; provisional flag | Panel / click | No AI | Curated table; static text | Provisional blocks sign-off | – | ~$0 | Build |
| Lint: fact without page ref | Pause/save | Rules + small AI | Regex (dates, values, drugs without citation object) + LLM sentence classifier | Inline warnings | Dismiss with reason | <$0.01/para | Build |
| Lint: loaded words; outside expertise | Save | Rules + AI | Lexicon ("clearly", "negligent", "failed") + LLM-1 context; LLM-1 vs profile specialty | Warnings | Expert decides | <$0.01 | Build |

### 16 Your report, 17 Final check, 18 Sign and send, 18b Time and invoice
| Capability | Trigger | AI? | Options | Downstream | Human check | Cost | B/B |
|---|---|---|---|---|---|---|---|
| Assemble into own template | Button | No AI | docxtpl/python-docx; paragraphs in content controls (`w:sdt`) with stable IDs; Aspose.Words/docx4j if fidelity fails | Draft DOCX v(n) | Preview | <10 s | Build |
| Page vs paragraph citation toggle | Toggle | No AI | Re-render citation objects | New version | – | ~$0 | Build |
| Open in Word + round-trip | Download/upload | No AI | Diff by content-control ID; read `w:ins`/`w:del`; flag text outside controls | Imported as versions; citations re-validated | Expert resolves every flag | <10 s | Build |
| PD35 checklist; methodology appendix; page count vs limit | Assembly | No AI | Presence checks; appendix from logs (model IDs, versions, region, checks, pages read, requests); LibreOffice→PDF page count | Checklist + appendix | Expert edits | ~$0 | Build |
| 28 final checks | Button | Mostly No AI | Deterministic: age vs DOB regex, unanswered Qs, citation→verified fact→read page, normalised verbatim quote match, all pages read, guidance in-force range, points against per question, SoT hash. AI: loaded language and expertise linters | Pass/fail list | Near-match quotes shown side-by-side | ~$0.05–0.2/run | Build |
| Colleague spot-check of 10 facts | Button | No AI | Random 10 cited facts; 72 h link + email OTP; watermarked page crops only | Responses logged | Needs instructing party's agreement | ~$0 | Build |
| Apply signature; confirm SoT | Button + re-auth | No AI | Image + typed full name (PD 22 3.8) | `sot_confirmed_at` | Preview | ~$0 | Build |
| Lock version | Button | No AI | SHA-256 of DOCX + PDF stored in an append-only, hash-chained Postgres table; write-once Supabase Storage path (no update/delete policy); optional RFC 3161 timestamp | Immutable; changes only via 21b | – | ~$0 | Build |
| Download; send to solicitor | Button | No AI | LibreOffice PDF/A; own-mailbox send or expiring OTP link (preferred for health data) | `served_at` → 19 deadlines | Confirm recipient | ~$0 | Build |
| Active-time tracking; rate vs estimate; invoice | Background / button | No AI | Heartbeat every 30 s when visible and input in last 2–5 min; stage = screen; WeasyPrint/Typst invoice | `time_entry`, `invoice` | Expert adjusts (audited) | ~$0 | Build |

### 19 After serving, 20 Part 35 questions, 21 New records/opposing report, 21b Corrections
| Capability | Trigger | AI? | Options | Downstream | Human check | Cost | B/B |
|---|---|---|---|---|---|---|---|
| Deadline tracker; countdown; view locked version; record correction | `served_at` + directions | No AI | Date arithmetic; Resend reminders via scheduled job; locked-version view | Deadline rows | Expert enters directions dates | ~$0 | Build |
| Ingest Part 35 questions | Upload | AI | OCR-1 + LLM-1 verbatim numbered extraction | `written_questions` row; countdown | Expert confirms | <$0.1 | Build |
| Clarifying vs going beyond report | After ingest | AI suggestion | LLM-1 | Label + reason | Legal judgement; expert decides | <$0.02 | Build |
| Link to report paragraphs | After ingest | AI | EMB + LLM-1 | Links | Expert confirms | <$0.05 | Build |
| Dictated answers with citations; "Tell the solicitor" | Mic / button | AI (STT) / template | STT-1 + fact chips; 08b send path | Supplementary answers → sign via 18 | Expert writes | ~$0.02/min | Build |
| New pages appended + "may matter" highlight | Upload | AI | 07 pipeline + EMB/LLM-1 relevance to questions | Appended numbering; flags | Expert reads all | ~$0.03/page | Build |
| Opposing report: extract claims + page refs | Upload | AI | LLM-1 atomic claims with spans | `opp_claim` rows | Expert reviews | ~$0.5 | Build |
| Verify each (wrong page / wrong value / not on page) | After extraction | AI + rules | Map refs via concordance → string/number match on page → search ±N pages/bundle (EMB) → LLM-1 NLI with both spans | Status + evidence | Expert confirms every discrepancy | ~$1–2/report | Build |
| Compare positions per question | After extraction | AI (quote-anchored) | LLM-1 aligns paragraphs; verbatim side-by-side, no synthesis | Comparison table | Expert interprets | ~$0.3 | Build |
| Correction vs change; old/new wording; reason required | Form | No AI | diff-match-patch; mandatory reason | Version type | – | ~$0 | Build |
| Dependent paragraphs | On change | Rules + AI | Shared fact citations, explicit cross-refs; LLM-1 for semantic dependency | Dependency list | Expert reviews each | <$0.1 | Build |
| Supplementary report with declaration + SoT; sign/send | Button | No AI | Template → 18 flow | New locked version | – | ~$0 | Build |

### 22 Joint statement
| Capability | Trigger | AI? | Options | Downstream | Human check | Cost | B/B |
|---|---|---|---|---|---|---|---|
| Ingest agenda | Upload | AI | OCR-1 + LLM-1 | `agenda_item` rows | Expert confirms | <$0.1 | Build |
| Prefill from served report | After ingest | AI (retrieval only) | EMB + LLM-1 selects **verbatim** paragraphs | Marked "from served report para X" | Expert edits | <$0.2 | Build |
| Agreed/not agreed with reasons; standard declarations | Form | No AI | Templates | – | Expert writes | ~$0 | Build |
| Detect deviations from served report | Save | AI | LLM-1 NLI vs linked paragraphs | "Changed position" warning | Expert confirms or records change | <$0.1 | Build |
| Deadlines (7/14 days) | Discussion date | No AI | Configurable from directions | Reminders | Check against order | ~$0 | Build |
| Co-signature | Button | No AI | Dropbox Sign / DocuSign / Adobe Acrobat Sign API; MVP: counterpart signing | Webhooks → `signed_by_both`; certificate stored + hashed | External party; check DPA/region | ~$0.5–3/envelope | Buy |

### 23 Trial page numbers, 24 Trial pack, 25 Witness box, 26 Close
| Capability | Trigger | AI? | Options | Downstream | Human check | Cost | B/B |
|---|---|---|---|---|---|---|---|
| Ingest ~3,914-page trial bundle | Upload | AI (OCR only) | OCR-1 **Read**; OCR-2 text | Trial pages stored; no extraction | – | ~$6/4,000 pages; 10–20 min | Buy |
| Match pages by content | After OCR | No AI (optional LLM tiebreak <5%) | MinHash/LSH candidates → pHash → **order-preserving sequence alignment** → rapidfuzz verify | `concordance(old→trial, score, method)` | Review low scores; blank/template pages use neighbours | ~$0–2 | Build |
| Concordance; missing pages; tell solicitor; print | After match | No AI | Table/CSV/PDF; email template | All citations show both numbers | Expert confirms | ~$0 | Build |
| Large-print trial pack | Button | No AI | WeasyPrint/Typst 14–16 pt: report with trial numbers, chronology, readiness sheets, joint statement, answers, legal tests, optional guidance | Pack PDF hashed | Expert reviews | 10–60 s | Build |
| "Nothing new on the day" | Button | Rules (+ optional AI) | Each note sentence traced to a served document (string/EMB); LLM-1 for paraphrase | Unsourced notes flagged | Expert removes/confirms | <$0.1 | Build |
| Offline read-only search | "Prepare for court" | No AI | PWA + service worker; SQLite-WASM (OPFS) FTS5 or IndexedDB + MiniSearch; WebCrypto AES-GCM with passphrase-derived key; auto-expiry; remote revoke | Device package: served docs, OCR text, both numberings, low-res cited pages | Seek court permission via solicitor; device loss in DPIA | 1–5 min build | Build |
| Export record of work | Button | No AI | ZIP: versions + hashes, methodology, read log CSV, invoice, request log | Audit row | – | ~$0 | Build |
| Retention choice; scheduled deletion; certificate | Choice / scheduler | No AI | Scheduled job (Supabase pg_cron or DigitalOcean App Platform scheduled job) calls FastAPI; deletes Storage objects, rows, embeddings, OCR JSON, packages; per-case data key (envelope encryption) destroyed for crypto-shredding | Signed certificate (counts, hashes, timestamps, backup-expiry date) emailed to expert + solicitor | Certificate must state backup expiry truthfully | ~$0 | Build |
| Archive and reopen | Button | No AI | Archived flag; files stay in Supabase Storage | State change; deletion recalculated | – | ~$0 | Build |

### Cross-cutting (all No AI except identifier stripping)
- **Hosting:** Vercel serves the frontend only; the browser calls FastAPI directly, so no patient data passes through Vercel. FastAPI API + worker run on DigitalOcean App Platform, London region: managed containers deployed from GitHub, no SSH, OS patching handled by DigitalOcean.
- **Server-side only:** the browser talks to FastAPI and nothing else. It never calls Supabase, Azure or AWS, holds no Supabase keys, and receives no direct storage links. The frontend only renders screens and sends user actions.
- **Database, auth, files:** Supabase project created in **London** (cannot be moved later). Tables live in a schema not exposed to Supabase's public Data API; row-level security stays on as a second line of defence; service-role key only in FastAPI; private Storage buckets read and written only by FastAPI; point-in-time recovery on a paid plan.
- **Encryption:** Supabase encrypts data at rest; TLS 1.2+ everywhere; app-level envelope encryption with a per-case data key for page text and images (enables crypto-shredding at deletion).
- **UK residency:** Supabase London, DigitalOcean London, Bedrock London in-region model IDs only (IAM policy denies `global.*`/`eu.*` inference profiles), Azure UK South.
- **Audit and versioning:** hash-chained append-only audit tables in Postgres; event-sourced versioning.
- **Jobs:** Postgres queue in Supabase (Procrastinate or pgmq) consumed by the App Platform worker, with idempotent per-page jobs.
- **Email:** Resend, patient-free content only ("Your bundle is ready" + login link). Resend is not UK-hosted, so never include case details.
- **Errors:** Sentry EU with PII scrubbing; never log prompts, OCR text or AI output. DigitalOcean app logs must also stay free of patient text.
- **Billing:** Stripe for expert billing only.
- **Analytics:** PostHog EU cookieless, or Plausible.
- **Identifiers:** Presidio (in the FastAPI worker) before every LLM call; mask identifier boxes on images sent to the VLM.

---

## 3. Vendor comparisons

**OCR / document AI**

| Option | Handwriting | Boxes + confidence | UK processing | Per 1,000 pages | Verdict |
|---|---|---|---|---|---|
| **Azure Document Intelligence** | Yes | Word polygons | UK South | Read $1.50; Layout $10 | **Primary** |
| AWS Textract | Yes | Word geometry | London | $1.50 / Layout $4 / Tables $15 | Strong all-AWS alternative |
| Google Document AI | Yes | Yes | Verify per processor | OCR $1.50; Layout $10 | Viable |
| Mistral OCR 4/4.1 | Good | Paragraph boxes | EU only | $4 ($2 batch) | Not UK |
| Claude/Gemini vision | Best on messy handwriting | None native (align to OCR words) | London | ~$10 (modelled) | Fallback only; fluent hallucination risk |

Microsoft's newer Azure Content Understanding is listed by one analysis as 33–50% cheaper. Check UK South availability before moving to it.

**LLM platforms**

| Platform | UK in-country inference | Retention | Solo-founder fit |
|---|---|---|---|
| **Bedrock Claude Sonnet 5 / Opus 5.5 London** | Yes | Zero operator access by Anthropic; no training | **Yes**, self-serve |
| Vertex Gemini 3.5 Flash europe-west2 | Yes (this model only) | ZDR route (disable caching) | Secondary |
| Azure OpenAI UK South | Some models | 30-day abuse monitoring unless approved | Hard |
| Mistral La Plateforme | No (EU default) | 30 days; ZDR on Scale plan | EU only |
| Anthropic API direct | No UK/EU option | – | No |

**Speech-to-text:**
- **Azure Speech (primary):** en-GB, UK South, Custom Speech phrase lists, 5 free hours a month.
- **Deepgram Nova-3 Medical:** lists en-GB, EU endpoint GA January 2026. You must opt out of model improvement on every request.
- **AssemblyAI Medical Mode:** EU endpoint "guarantees your data never leaves the European Union"; $0.36/h async.
- **Whisper self-hosted:** offline fallback.
- **UK medical dictation vendors (Dragon Medical One / Dragon Copilot):** strong vocabularies but per-seat licences and no simple API. Experts may already own Dragon, so let them type with their own tool.

**Embeddings/reranking:** small in-region models + pgvector are ample for 1–4k pages; cost under $0.20 per case.

**Email send-as-user:**
- **Microsoft Graph:** delegated `Mail.Send` + `offline_access`. Complete publisher verification: Microsoft's publisher verification overview states that "Beginning November 2020, if risk-based step-up consent is enabled, users can't consent to most newly registered multitenant apps that aren't publisher verified." Don't request `Mail.ReadWrite`.
- **Gmail:** request only `gmail.send` (sensitive scope: brand, privacy policy, demo video, no CASA). `gmail.compose`/`gmail.readonly` are restricted and require an annual CASA assessment.
- **MVP:** ship `mailto:` / .eml and log it.

**E-signature and UK law:**
- **Law:** CPR 5.3 allows mechanical signature. PD 5B para 5.1 accepts a typed name or an electronically applied facsimile for emailed documents. PD 22 para 3.8 requires the full name printed beneath. The Law Commission (2019) confirmed that e-signatures can satisfy statutory requirements. A drawn or uploaded image plus typed name is therefore valid for the expert's own report. Counterpart signing is the simplest route for joint statements.
- **Vendors:** DocuSign offers EU data-centre residency for enterprise accounts and a DPA with the UK transfer addendum, but "limited data may be replicated outside your data region". Dropbox Sign and Adobe Acrobat Sign are API-friendly with EU data centres. No UK-only option was found, so treat signing as an EU transfer and upload only the final document.

**DOCX:**
- python-docx/docxtpl (MIT) for templates.
- docx4j (Apache) for content controls.
- Aspose.Words (commercial) for highest fidelity.
- LibreOffice headless for PDF and page counts (minor drift from Word; label page count "approximate").
- Microsoft Graph/Word Online puts files in the user's tenant; avoid for MVP.

**PDF:**
- Viewing: PDF.js, or your own rendered tiles.
- Splitting: qpdf/pikepdf.
- Rendering: pypdfium2.
- Generation: WeasyPrint/Typst.

**NICE versions and licence (checked 15 Oct 2026):**
- **UK Open Content Licence:** free ("UK-only, royalty-free, perpetual, non-exclusive"), commercial use allowed, attribution and NICE's disclaimer required, no display next to advertising. It **excludes AI use**, Clinical Knowledge Summaries and the BNF.
- **Syndication API:** free for delivering content in the UK; a fee applies only for international use. It provides current guidance only; previous and withdrawn versions are not available through it.
- **AI use:** needs approval and a licensing arrangement; NICE publishes no UK fee (fees are stated for international use). Apply via the syndication API application form, or email reuseofcontent@nice.org.uk describing how AI is used, how outputs are used and where they appear.
- **Approach for Avodeck:** a curated `guideline_version` table (guideline → version → in force from/to → link), starting with the 20–30 guidelines that dominate GP claims (NG12 suspected cancer, NG51 sepsis, head injury, chest pain, fever in under-5s, VTE). Screens 12 and 15 show the version label and link only; no NICE text goes to Claude until NICE approves AI use. Apply now, because approval takes time.

---

## 4. UK compliance matrix

| Vendor | UK processing | EU processing | Retention | DPA | Patient-data fit |
|---|---|---|---|---|---|
| **Bedrock Claude Sonnet 5/Opus 5.5 London in-region** | **Yes** ("never leaves that Region") | – | No training; zero operator access; keep Bedrock invocation logging off | AWS DPA | **Primary** |
| Bedrock other Claude from London | No (Geo/Global) | EU Geo | As above | AWS DPA | EU transfer only |
| Vertex Gemini 3.5 Flash europe-west2 | Yes (secondary reports) | – | ZDR route | Google Cloud DPA | Secondary |
| Azure OpenAI UK South | For available models | – | 30 days unless Modified Abuse Monitoring (gated, EA/MCA) | Microsoft DPA | Hard |
| Azure Document Intelligence / Speech | UK South | – | Transient; disable data logging | Microsoft DPA | **Primary OCR/STT** |
| Mistral | No | Yes (non-EU subprocessors "in exceptional cases") | 30 days; ZDR Scale plan | Mistral DPA | EU only |
| Deepgram | No | EU endpoint | Opt out per request; default list price opts in | DPA | EU only |
| AssemblyAI | No | EU endpoint | BAA/EU customers opted out of training | DPA | EU only |
| DocuSign | Not found | EU data centres (enterprise) | Retains envelopes | DPA + UK addendum; BCRs | Final docs only |
| Supabase (London project) | Yes | – | Your data; backups in region | Supabase DPA | **Primary DB, auth, storage** |
| DigitalOcean App Platform (London) | Yes | – | Logs: keep patient-free | DigitalOcean DPA | **Primary API + worker** |
| Vercel | Global CDN | – | Request logs | Vercel DPA | Frontend only, no patient data |
| Resend | No | – | Email content retained | Resend DPA | Patient-free email only |
| Stripe / PostHog EU / Sentry EU | n/a | – | – | DPAs | No patient data |

**Obligations (selling to experts, not the NHS):**
- **Roles:** the expert/solicitor is controller and Avodeck is processor. Sign an Article 28 agreement with each expert, flow it down to subprocessors, and publish the subprocessor list.
- **ICO:** pay the data protection fee. Per the ICO's guide to the fee, Tier 1 (turnover ≤£632,000 or ≤10 staff) is £52, Tier 2 is £78 and Tier 3 is £3,763, with "an automatic discount of £5" when paying by direct debit.
- **DPIA:** mandatory for high-risk processing including large-scale health data and novel technology. Cover the LLMs, offline devices, colleague sharing and e-signature transfers.
- **Lawful basis:** typically Article 9(2)(f) (legal claims).
- **DSPT:** required for organisations with access to NHS patient data or systems under NHS arrangements. Avodeck is outside NHS contracts, so it is **not mandatory**. A free Category 3 submission still adds credibility, and DTAC (v2, 2026) would require it if you ever sell to NHS bodies.
- **Cyber Essentials:** not legal, but commonly requested by law firms.

---

## 5. Accuracy evidence

**OCR:**
- **Modern handwriting is easy for LLMs.** Crosilla et al. (2025) report GPT-4o-mini at 1.71% CER / 3.34% WER on IAM.
- **"In the wild" handwriting still lags.** WildHandBench (2026) shows a "persistent handwritten gap", with the best of 18 models at 71.85%.
- **Doctor handwriting is hardest.** An independent 2026 prescription test found traditional engines "unusable" and the best open models at ~20–33% exact match.
- **Vendor claims of 85–95% VLM accuracy (Extend) are marketing.**

Expect many handwritten pages to land in the screen 10 queue, by design.

**LLM extraction and temporal reasoning:**
- **Medication extraction is strong; harder relations are not.** GPT-4 macro-F1 0.962 on ophthalmology notes, versus 53% F1 (GPT-4o, n2c2-2018, preprint) for adverse-drug-event relations.
- **Temporal reasoning (TIMER, Stanford, 2025):**
  - Claude 3.5 Sonnet: 89.6% correct on TIMER-Bench, but 69.0% correct / 57.8% complete on clinician-written MedAlign questions.
  - GPT-4o: 85.3% / 70.7%.
  - Failures include "chronological confusion".
- **Temporal relations:** best micro-F1 89.54% (GatorTron, i2b2 2012).

Never let the LLM decide availability on a date; compute it.

**Grounding:**
- **TORTUS (npj Digital Medicine 2025):** 1.47% hallucination (44% major) and 3.45% omission rates in clinical notes, reduced below human error rates by prompt and workflow refinement.
- **SourceCheckup (Nature Communications 2025):** "between 50% and 90% of LLM responses are not fully supported, and sometimes contradicted, by the sources they cite"; doctors judged 40.4% fully supported.

Adopt OCR-word-ID citations, server-side value-in-span checks, normalised verbatim quote matching, NLI with both spans shown, verified-only citation, and LLMs that label supplied fact IDs rather than generate facts.

**Speech:**
- **O'Kane et al. (King's College London preprint, May 2026; 800 transcripts of read-aloud surgical case reports):**
  - Clinically significant errors in 27% (GPT-4o Transcribe + GPT-5 correction) to 65% (Amazon Medical Transcribe) of transcripts.
  - Whisper 40%; Dragon Medical One 52% in the table (65% in the abstract, an inconsistency).
  - Medical-term WER 3.65% best vs 24.03% worst.
  - 35 errors could have caused severe harm or death, 37.1% involving medication type or dose.
- **Vendor claims (unverified):** Deepgram Nova-3 Medical median WER 3.44%; AssemblyAI Medical Mode 3.2% missed-entity rate and 5.3% WER on its own data.

---

## 7. Cost model (modelled, USD, list prices October 2026)

**Per 1,000-page bundle**

| Item | Assumption | Cost |
|---|---|---|
| OCR (Azure DI Layout) | 1,000 × $10/1k | $10.00 |
| Vision fallback | 10% of pages × ~$0.011 | $1.10 |
| Per-page extraction (Sonnet 5 in-region) | ~0.8k fresh + 3k cached input + 0.6k output per page at $2.20/$11 | ~$9.00 (~$4.50 batch) |
| Case-level reasoning | ~300k mostly cached input + 30k output | $1–2 |
| LOI, linting, Part 35, opposing report (case lifetime) | | $2–4 |
| Embeddings, dedup, PII, rendering | CPU | <$0.50 |
| Dictation | 3–5 h Azure real-time | $3–5 |
| **Total** | | **≈$27–32 standard; ≈$20–25 with batch** |

The 1,268-page example comes to ≈$33–40. A 4,000-page trial bundle costs ≈$7–10 (Read OCR $6 + optional tiebreaks), with no extraction.

**Monthly (1.5 active cases per expert per month)**

| Experts | Cases | AI | Hosting (Vercel, App Platform, Supabase, Resend) | **Total** |
|---|---|---|---|---|
| 10 | 15 | ~$525 | ~$100–150 | **~$650** |
| 50 | 75 | ~$2,600 | ~$250–400 | **~$3,000** |
| 200 | 300 | ~$10,500 | ~$800–1,200 | **~$11,500** |

These exclude fixed annual costs: penetration test (a few £k), Cyber Essentials (the IASME basic assessment fee is £320 + VAT for micro organisations of 0–9 staff and £440 + VAT for 10–49 staff, per secondary 2026 summaries of IASME's fee bands; not confirmed on IASME's own site), ICO fee, legal review, insurance and optional Aspose/e-signature licences. AI cost scales with pages, so price per case or per 1,000 pages.

---

## 8. MVP vs later

**MVP:**
- 01 auth with mandatory MFA.
- 02 profile and signature.
- 04 upload.
- 05 conflicts.
- 06 LOI questions.
- 07 full pipeline.
- 08 numbering, provenance and missing list with `mailto:`.
- 09 reader with server-enforced blind read, bounding-box facts, verification states and read log.
- 10 typed transcription.
- 11 basic timeline.
- 13 four rule-based checks.
- 15 chips, citation lint and dictation.
- 16 assembly, PD35 checklist and methodology.
- 17 deterministic checks.
- 18 sign, lock, hash and download.
- 18b time tracking.
- 26 export, deletion and certificate.

**Phase 2:**
- 12 availability reconstruction + NICE table.
- 14 points against and conflicts.
- Remaining 13 checks + rule builder.
- Word round-trip.
- 19–21b post-service workflows.
- Graph/Gmail send.

**Phase 3:**
- 22 e-signature.
- 23 concordance.
- 24 trial pack.
- 25 offline mode.
- Colleague spot-check.
- Accounting integrations.

## Recommendations

1. **Host on Vercel + DigitalOcean App Platform London + Supabase London; use Bedrock London in-region + Azure UK South as AI APIs.** Block every non-UK Bedrock route in IAM.
2. **Make provenance structural.** Extraction must return OCR word IDs and the server rejects values not present in them. Only verified facts are citable, and quotes are string-verified.
3. **Compute temporal availability and "guidance in force" deterministically.** Show "unknown" honestly.
4. **Curate NICE versions by hand.** UK reuse is free under the Open Content Licence, but AI use needs NICE's approval; apply now and keep NICE text out of Claude until approved.
5. **Ship `mailto:` first.** Then add `gmail.send` and Graph `Mail.Send` with verification.
6. **Before the first real bundle:** complete the DPIA, register with the ICO, get Cyber Essentials and offer an Article 28 DPA. DSPT Category 3 is optional.
7. **Treat every "Not right / Change a detail" click as labelled evaluation data.** Report precision and handwriting-queue rates per release in the methodology appendix.

## Caveats

- **Confirm hosting regions at sign-up:** Supabase London, DigitalOcean App Platform London for every component, and each provider's DPA.
- **Re-verify availability before launch.** London in-region model lists changed on 30 September 2026 and Azure UK South availability shifts. Haiku 5.5 London availability is unconfirmed.
- **Some evidence is secondary.** Gemini 3.5 Flash UK processing, the Azure OpenAI UK constraints (community Q&A), Sonnet 5 regional pricing and some OCR prices come from secondary sources; confirm in live vendor documentation and contracts.
- **Costs are modelled.** Run a 200-page consented pilot to calibrate tokens per page.
- **No benchmark covers UK GP printouts or handwritten nursing notes.** Build your own test set. Vendor speech figures are self-reported, and the UK speech study is a preprint.
- **Some procedural points depend on the court.** Joint-statement timings follow court directions. Device use in the witness box is at the judge's discretion. Colleague spot-checks disclose records to a third party.
- **This is a technical map, not legal advice.** Have the DPIA and processor documents reviewed by a UK data-protection solicitor.

---

---

## Multi-country notes

## 12. Built for more than one country: jurisdiction packs and regional cells

Avodeck launches for England and Wales, but nothing in the code may assume it. Two seams make a later move to Australia, Canada or New Zealand a matter of adding content and a deployment, not rewriting slices.

### 12.1 What changes between countries

| Area | England and Wales (launch) | Elsewhere (examples) |
|---|---|---|
| Expert rules | CPR Part 35, PD35 | Australia: Federal Court expert evidence practice note and state codes of conduct. Canada: provincial rules (e.g. Ontario r. 53.03 and its acknowledgment form). NZ: High Court code of conduct for expert witnesses |
| Declarations | Statement of truth, PD35 wording | Different wording and signed acknowledgments |
| Written questions | Part 35 questions, 28 days | Different processes and time limits |
| Expert meetings | Joint statement | Joint reports, conclaves, concurrent evidence (Australia) |
| Legal test | Bolam/Bolitho, Montgomery | Australia: peer professional opinion under state civil liability acts; Rogers v Whitaker. Canada: its own case law |
| Guidelines | NICE | National/state/provincial bodies, each with its own licence |
| GP record systems | EMIS, SystmOne | Best Practice, MedicalDirector (AU); Accuro, TELUS PS Suite, OSCAR (CA); Medtech, Indici (NZ) |
| Dates | DMY | Canada mixes YYYY-MM-DD and MM/DD |
| Vocabulary | GP, claimant, solicitor, trial bundle, paracetamol | Family physician, plaintiff, counsel, court book, acetaminophen |
| Patient identifiers | NHS number | Medicare (AU), provincial health card (CA), NHI (NZ) |
| Privacy law | UK GDPR, DPA 2018 | Privacy Act + state health records acts (AU); PIPEDA + provincial health laws (CA); Privacy Act 2020 + Health Information Privacy Code (NZ) |
| Tax and currency | GBP, VAT | AUD/GST, CAD/GST-HST, NZD/GST |

**Market notes to verify before choosing a country:**
- **New Zealand:** ACC's no-fault scheme largely bars treatment-injury compensation claims, so expert work centres on complaints, disciplinary, coronial and ACC reviews.
- **Canada:** Quebec is civil law and French-speaking; leave it out at first.
- **Australia** is the closest fit to the UK and the natural second market.

### 12.6 Cost of doing this now vs later

| | Now (in the foundation) | Later (per new country) |
|---|---|---|
| Work | About 1–2 extra weeks: jurisdiction field, pack interface, `gb-eaw` pack, i18n keys, cell config, neutral names | Pack content 3–6 weeks with a local legal/clinical adviser; new cell 1–2 weeks; local synthetic bundles and evaluation; local compliance |
| Risk if skipped | — | "UK" baked into every slice; months of refactoring and re-testing |

This section is a planning map, not legal advice. Before entering a country, confirm the court rules, health-data location expectations, market fit (especially NZ) and in-region AI model availability with local advisers.

---

## Market size (October 2026)

| Country | Clinical negligence claims a year | Notes |
|---|---|---|
| England | 15,236 new NHS claims in 2025–26; £3.24bn paid ([Legal Futures](https://www.legalfutures.co.uk/latest-news/nhs-bill-for-legal-costs-flatlines-as-number-of-claims-hit-new-record)) | Often two or more experts per claim |
| Canada | About 730–900 new legal actions against doctors a year, 2016–2020 ([CMPA](https://www.cmpa-acpm.ca/static-assets/pdf/about/annual-report/2020/20-annual-report-by-the-numbers-5yr-trend-e.pdf)) | Regulatory matters 4,000–5,600 a year, rarely full expert reports |
| Australia | 1,620 new public-sector claims in 2009–10 ([AIHW](https://www.aihw.gov.au/reports/hospitals/australia-s-public-sector-medical-indemnity-claims/contents/summary)) plus private | No recent combined figure; the larger opportunity is the IME and compensation-scheme market (see below) |
| New Zealand | Very few | ACC no-fault scheme |

## Expert types (plain terms)

- **Breach of duty:** did the care fall below a reasonable standard? Same specialty as the clinician.
- **Causation:** if care had been right, would the outcome have been different (on the balance of probabilities)? Often a different specialty.
- **Condition and prognosis:** what is wrong now, how it will develop, what is needed; usually examines the person; drives compensation.
- **Personal injury:** accidents not involving medical care (road, work, public places, industrial disease); mainly causation and condition-and-prognosis, with pre-existing conditions central. Low-value whiplash goes through MedCo (fixed fees, templated) and is out of scope.

## Where the engine could go later

| Area | Fit | Extra work | View |
|---|---|---|---|
| Other clinical negligence specialties | Excellent | Specialty pack + evaluation | Next |
| Causation and condition-and-prognosis (clinical negligence) | Very good | Slice J | After that |
| Personal injury (non-whiplash) | Good | Slice J + before/after view | Later |
| Law firms (medical chronologies) | Very good | Team accounts, different buyer | Decide later; may unsettle experts |
| NHS incident and mortality reviews | Excellent | NHS assurance (DSPT, DTAC), procurement | Strong but slow |
| Regulators / fitness to practise | Very good | Different report formats | Later |
| Coroner's inquests | Good | Small | Later |
| Insurance claims (life, critical illness, income protection) | Technically good | Different buyer and ethics | Off-brand |
| Other courts (family, employment, criminal) | Moderate | New packs | Much later |

## IME and compensation schemes: Australia and Canada (researched 15 Oct 2026)

**Verdict: later, but yes for Australia; Canada after that.** The work fits about two-thirds of the engine. It is high in volume but low in value per report, because fees are capped. Start with a Victoria pilot (WorkSafe Victoria plus TAC), not NSW.

### Size

| | Australia | Canada |
|---|---|---|
| Claims feeding medical assessments | About 139,000 serious workers' compensation claims a year, 2022–23 ([Safe Work Australia](https://www.safeworkaustralia.gov.au/media-centre/media-release/key-work-health-and-safety-statistics-2024-media-release)), plus CTP/TAC, life insurance (TPD and income protection) and litigation | 348,747 accepted lost-time claims ([AWCBC](https://awcbc.org/files/fact-sheets/AWCBC_FactSheet_NWISP-2024-EN.pdf)), plus auto accident benefits |
| Best hard figure | NSW: about 26,000 workers' compensation claims had an IME in 2022; NSW medico-legal spend about A$81m in 2022 (WC non-ILARS about $50m, ILARS about $19m, CTP about $12m) ([SIRA benchmarking, Nov 2023](https://www.sira.nsw.gov.au/__data/assets/pdf_file/0010/1305838/Medico-legal-fee-benchmarking-analysis-Nov-2023.pdf)) | No national count of assessments; workers' compensation boards mostly use in-house medical advisers |
| Personal Injury Commission (NSW) | 2025–26: about 14,000 workers' compensation and 7,500 motor accident registrations, up 27% and 19% ([PIC](https://www.pi.nsw.gov.au/news/2026/record-filings-in-2025-26)) | — |
| Total reports (**estimate, medium-low confidence**) | 80,000–120,000 scheme IMEs a year; A$250–400m including life/TPD and litigation | Smaller relevant share: auto insurers (Ontario insurer examinations) and disability insurers |

### Fees (capped)

- **NSW workers' compensation** ([SIRA fees order, Feb 2024](https://www.sira.nsw.gov.au/__data/assets/pdf_file/0007/1280653/Medical-examinations-and-reports-fees-order-effective-1-february-2024.pdf)), excluding GST:

  | Report | GP | Specialist |
  |---|---|---|
  | Standard | A$669.60 | A$904.60 |
  | Complex | A$999.60 | A$1,798.90 |
  | Psychiatric | — | A$1,582.60 |
  | File review | A$495.50 | A$677.80 |
  | Supplementary | A$330.50 | A$451.60 |

  **No extra allowance for reading time:** the effective rate is about A$340/hour, against up to about A$900/hour for uncapped public liability work. This is the core selling point.
- **NSW CTP:** IME about A$1,800; joint examination about A$2,476.
- **Victoria (WorkSafe and TAC):** published fee schedules by discipline. The 2026 PDF has not yet been reviewed.
- **Ontario auto insurance (SABS):** C$2,000 cap per assessment including the report, under section 25(5)(a).

### NSW is changing (2025 reforms, passed)

- **From 1 July 2026:** one permanent impairment assessment per incident, and insurers can no longer require a worker to attend one.
- **From mid-2027:** SIRA appoints a principal assessor from a public register, and assessments arranged outside this process are not funded.
- **Psychological injury threshold:** rises from 15% to 25% (2026), 27% (2027) and 28% (2029).

Source: [SIRA reform FAQs](https://www.sira.nsw.gov.au/workers-compensation/workers-compensation-reform-faqs). NSW volume and structure are unsettled until 2027.

### Players and software

- **Agencies:** Medilaw (part of MedHealth) does 8,000+ IMEs a year. TAC routes all IME bookings through two intermediaries: **MEDirect** and **Medicolegal Express** ([TAC](https://www.tac.vic.gov.au/providers/type/medical-examiners-and-assessors/delivering-services/independent-medical-examiners)). Canada: AssessMed, Synoptic, SOMA, CBI Health, Ducore (Quebec) and others.
- **MEDirect:** booking, dictation into templates, invoicing and a marketplace. No record review, chronology or provenance, so it is a likely partner.
- **Wisedocs** (Toronto): record sorting and summaries for insurers. C$21.4m raised, about 50 customers, 70–80% in the US, "conversations underway in Australia" ([BetaKit](https://betakit.com/ai-powered-medical-record-reviewer-wisedocs-closes-12-7-million-cad-series-a-round/)).
- **Loom Analytics' Claudio** (Toronto): IME transcription and AI-drafted reports for IME firms ([Loom](https://www.loomanalytics.com/medicolegal)). The opposite of our rule 1.
- **No one found** offers word-level provenance, enforced reading or an audit trail aimed at the examining doctor.

### Rules

- **NSW Supreme Court Practice Note SC Gen 23** (from 3 Feb 2025): generative AI may not draft or produce the content of an expert report, or any part of one, without the court's permission. If permission is granted, the expert must disclose which parts used AI and keep records of how it was used. Spellcheck and **software that creates chronologies from source documents** are excluded from its definition of generative AI ([Cowell Clarke](https://cowellclarke.com.au/insights/using-generative-artificial-intelligence-in-new-south-wales-courts-the-upcoming-dos-and-don-ts-for-practitioners-and-witnesses)). Avodeck's design fits; tools that draft reports don't. Get legal confirmation before using this in marketing.
- **AMA Guides:** licensing for software and calculators is not published; the AMA asks organisations to contact it ([FAQ](https://www.ama-assn.org/delivering-care/ama-guides/ama-guides-faqs)). Slice K (impairment calculator) is blocked until a licence is in place.
- **Hosting:** the Privacy Act has no strict localisation rule, but scheme and insurer contracts will expect Australian hosting; the AU cell covers this. Quebec requires a privacy assessment before data leaves the province, and the work there is in French. (General knowledge; verify.)

### Ranking and first pilot

1. **Victoria, WorkSafe plus TAC.** Stable, with shared quality assurance and standard report templates, and a ready channel through the two intermediaries.
2. **Life insurance TPD and income-protection file reviews.** Records-heavy, no examination, fees not capped.
3. **NSW workers' compensation and CTP.** Large, but unsettled until 2027.
4. **Queensland, WA and SA workers' compensation.** Smaller.
5. **Canada, Ontario auto insurer examinations.** Capped fees, competitors already established, consolidated providers.

**Pilot:** Victorian WorkSafe/TAC IMEs by orthopaedic surgeons or psychiatrists, through a MEDirect or Medicolegal Express partnership.

**Pricing idea:** A$50–80 per case or A$199–299 a month.

**Revenue at 100,000 reports a year and about A$60 per report:**

| Share of reports | Revenue a year |
|---|---|
| 1% | about A$60k |
| 5% | about A$300k |
| 10% | about A$600k |

**Kill criteria:**
- no intermediary integration within 6 months of approaching them;
- pilot doctors save less than 1.5 hours per report;
- no AMA Guides licence at a sensible price (this kills only slice K, not the pilot).

**What Avodeck needs:**
- base case-type pack `ime`, with scheme packs that use `extends:`;
- slice J (examination, consistency between the person's account and the records, before/after-injury view);
- slice K (impairment calculations, licence needed);
- agency team accounts (organisations already exist);
- an AU cell.

