# Synthetic evaluation cases

Real patient data never goes in this repo. Every bundle here is invented.

Layout: `evals/<jurisdiction>/<case-type>/<specialty>/bundles/<case>/` and `…/expected/`.

## Mrs A (`gb-eaw / clin-neg-breach / general-practice / synthetic-mrs-a`)

**The story.** A woman in her 50s sees her GP several times between February and August 2024 with tiredness and change in bowel habit. She is later diagnosed with colorectal cancer. The questions concern whether an urgent suspected-cancer referral should have been made earlier.

**Bundle:** 900 pages, 4,272 expected facts. Regenerate with `make eval-data` (deterministic; the PDFs are not committed).

Page numbers are positions in `records.pdf` (upload order), which are also Avodeck's permanent bundle page numbers.

What's in it, in the order a solicitor might send it:
- **Northam General (Trust A, classic typed letters):** clinic letters back to 2009, an Advice and Guidance reply with a received stamp and a GP's handwritten "Seen" note, ECG, imaging, typed lab reports.
- **Eastmere (Trust C, boxed e-discharge):** 2019 cholecystectomy, histology; Kingsmead private hospital letters; Riverside community services.
- **Westvale (Trust B, modern template):** older ED attendances.
- **Paper era:** 13 handwritten Lloyd George cards (1986 to 2003) in real handwriting fonts with different doctors' hands and inks; typewritten 1990s letters; two maternity episodes (1995, 1999) with booking form, scans, hand-held antenatal record, partogram, handwritten postnatal notes.
- **Castle Green Practice (SystmOne-style print):** 2014 to 2016.
- **GP correspondence:** screening letters, practice letters, insurance report, 111 and out-of-hours reports, pharmacy and optician letters, fit note, suspected cancer referral form.
- **Brookvale Surgery (EMIS-style):** brief summary; 100-page full summary (newest first) with consultations, results, documents, admin entries and online consultations; medication issues 2003 to 2024; appointments (with did-not-attends); tasks.
- **2024 cancer episode (Westvale):** clinic and MDT letters, CT and histology, pre-op, consent form, nursing assessment, operation note, handwritten ward notes, observation and drug charts, discharge summary, chemotherapy letters and bloods.
- **37 re-scanned duplicates** and about 160 routine one-page documents in 14 styles, spread through pages 1-450.

**Pages 451-900 (detailed records, appended so planted page numbers never move):**
- **The GP's own referral documents:** the Advice and Guidance request (25 April 2024) and the printed e-RS conversation showing when the reply was viewed; the routine gastroenterology referral letter (24 June) and the urgent suspected cancer referral (20 August), each with the attached practice summary.
- **The full 2024 inpatient record:** admission clerking, nursing notes three times a day for six days, fluid balance charts, day and night observation charts, anaesthetic record, surgical safety and pre-operative checklists, enhanced recovery pathway, IV iron record, PRN, IV fluid and VTE charts, pharmacy medicines reconciliation, physiotherapy notes, post-op bloods, pre-op ECG.
- **Oncology:** chemotherapy prescriptions and toxicity assessments for each cycle, day-unit nursing records, acute oncology telephone triage log, MDT outcome forms, consent, CNS notes, holistic needs assessment.
- **The 2019 gallbladder admission in full:** clinic letter, clerking, consent, anaesthetic record, operation note, nursing notes, fluid, observation and drug charts, pre-op bloods.
- **Maternity detail:** CTG strips, labour notes, community midwife visits for both births.
- **2003-2016 correspondence:** a dozen specialty letters in three trust styles (typewritten before 2006), the practice's own outgoing referral letters, casualty cards, ECG, imaging, Eastmere letters from the Castle Green years.
- **Therapy and community:** talking therapies discharge, handwritten physiotherapy and district nursing notes.
- **Printed lab copies** of about 120 GP results in two lab styles: same data as the GP print, different format, so near-duplicates that must **not** be set aside as duplicates.
- **45 more re-scanned duplicates** (82 in total) and about 140 routine pages.

**Mix:** 148 born-digital pages; the rest are scans (576), poor scans (114) or faxes (62).

### Planted items (every one is checked by a test)

| # | Item | Page(s) | Used by |
|---|---|---|---|
| 1 | 82 duplicate pages (same content, re-scanned) | `expected/duplicates.json` | A |
| 2 | Task list stops in January 2024 (records gap) | `expected/gaps.json` | A |
| 3 | Colonoscopy report mentioned in a letter but missing from the bundle | citing page in `expected/missing.json` | A |
| 4 | Low-confidence handwriting pages | `expected/unreadable.json` | A, B |
| 5 | 55 born-digital pages; one page with a typed header and a scanned body (page 93) | `expected/text_source.json` | A |
| 6 | Dates printed `03/04/2024` (3 April) | 82, and many routine pages dated day 1 to 12 | A |
| 7 | Consultation 15 May 2024: "review 3/52 if no better" (the canvas wording "x3/52") | 118 | B, D, E, I |
| 8 | Hospital letter dated 10 May, stamped received 22 May, handwritten "Seen RP 28/5/24" | 1 | D |
| 9 | Blood result filed 14 May (available before 15 May) | 118 | D |
| 10 | Safety-netting entry not used in the first draft | 117 | E |
| 11 | Ferritin 9 µg/L, no action within 14 days | 119 (GP print) and 402 (lab copy) | D, G |
| 12 | Referral 24 June (117) acknowledged only on 30 August (2) | `expected/findings.json` | D |
| 13 | Out-of-hours contact reached the GP the next day (should **not** be a finding) | 111 | D |
| 14 | Cited pages missing from the trial bundle | 214, 111, 119 | I |
| 15 | Identifiers: invented name, NHS number (mod-11 valid), postcode and DOB on every page header | all | A (PII tests) |

### Other synthetic documents

| Document | Contents | Used by |
|---|---|---|
| `letter-of-instruction.pdf` | 4 questions (Q4 causation) and 1 assumption | C |
| `template.docx` | The expert's own Word template | C, F |
| `witness-statement.pdf` | Includes "I was never told to come back" | E |
| `written-questions.pdf` | 6 questions; one asks for a new causation opinion | G |
| `appended-records.pdf` | 14 pages (numbered 901-914); one relevant new result | A, G |
| `other-expert-report.pdf` | 12 claims: 7 match, 2 cite pages that don't exist, 1 wrong value ("ferritin 19"), 1 contradicted, 1 matches only after Opus review | G |
| `agenda.pdf` | Includes an item that tests a changed position | I |
| `trial-bundle.pdf` | Re-ordered and re-paginated; 3 cited pages removed (including 214); 5 re-scanned at lower quality | I |
| `notes-for-trial.txt` | Includes one sentence found in no served document | I |

### Behaviour-test inputs (`inputs/`, built by `generator/inputs.py`)

Small separate test cases for the behaviour rules (architecture section 10). Expected results are in `expected/inputs.json`.

| File | What it tests | Slice |
|---|---|---|
| `mixed-records.pdf` | 20 pages: Mrs A pages 1-8, then 4 pages of another invented patient (different name, DOB and NHS number), then Mrs A pages 9-16. Pages 9-12 must be suggested for quarantine | A |
| `welsh-letter.pdf` | A page not in the pack's language: flag it, never machine-translate | A |
| `rotated-page.pdf` | Page 118 turned 90°: rotate, re-read, keep the number | B |
| `photographed-page.jpg` | A phone photo of page 214: image to page, offered as another copy, never merged automatically | A |
| `voicemail.wav` | An exhibit: stored, listed, never paged or read | A |
| `referral-letter.docx`, `further-records.eml`, `records-batch.zip` | Word, email with attachment, and zip inputs | A |
| `supplementary-letter-of-instruction.pdf` | Instruction version 2 with a new question (e): reopens the locked question set | C |
| `court-order.pdf` | Moves two deadlines and adjourns the trial: deadline history, hearing adjourned, packs stale | G |
| `written-questions-round-2.pdf` | A second round of questions with its own deadline | G |
| `agenda-v2.pdf` | Item 3 reworded: new agenda version, differences shown | I |
| `trial-bundle-v2.pdf` | Re-issued court bundle with a supplementary section: supersedes version 1 | I |

### Expected files

| File | Contents |
|---|---|
| `pages.json` | Everything per page: document, source, text source, scan quality, facts, dates, availability evidence, references |
| `sources.json` | Pack source type and detailed document type per page |
| `facts.json` | Every fact, with kind, value, page and word indexes. Hand-checked by a GP before use |
| `duplicates.json`, `missing.json`, `unreadable.json`, `text_source.json` | The planted items above |
| `availability.json` | Available-on date per letter or result |
| `findings.json` | Checks that should and shouldn't fire |
| `questions.json` | T-06 expected output |
| `planted.json` | Planted item number to pages |
| `struck_out.json` | Text crossed out on handwritten pages (must not become facts) |
| `references.json`, `gaps.json` | Documents referred to; the planted records gap |
| `appended.json`, `written_questions.json`, `opposing_claims.json`, `agenda.json`, `concordance.json`, `notes_check.json` | Expected answers for the other documents |

Facts give the exact quote on the page rather than word indexes, because word IDs only exist after OCR; tests find the quote's words in the OCR output. **A GP must review `facts.json` before tests rely on it.**

Clinical codes in the SystmOne-style print are invented placeholders.

**Handwriting** (`generator/hand.py`) is modelled on real clinicians' notes. It uses 29 open-licence handwriting fonts in `generator/fonts/` as raw material, then:
- gives each writer a fixed profile (slant, size, pressure, messiness);
- distorts every word differently so no two letters match;
- collapses doctors' word endings into wavy scrawl;
- lets baselines drift and writing crowd into the margin, with fatigue further along the line;
- adds biro skips and uneven ink.

Ward-round corrections appear as they do on real notes: a single line through an error with "error" and initials, scribbled-out words, wrong-patient blocks crossed with an X, struck plan items replaced, margin insertions with arrows, underlined headings and circled values. Struck-out text is listed in `expected/struck_out.json` and **must never be extracted as a fact**. Pages by the messiest writers are flagged in `unreadable.json`. Recorded OCR and Claude responses go in `backend/tests/fixtures/`.
