# Data Protection Impact Assessment

**Avodeck — AI-assisted medical records review for expert witnesses**

| | |
|---|---|
| Version | 1.0 |
| Date | 1 November 2026 |
| Author | Dr Aishat Abubakar, Director, Four Integers Ltd |
| Next review | 1 November 2027 |

> This DPIA follows the ICO's recommended structure under Article 35 UK GDPR. It is mandatory because Avodeck processes special-category health data using novel AI technology. Keep this document under review and update it whenever processing changes materially.

---

## 1. Description of the processing

### Nature of the processing

Avodeck is a web application that helps expert witnesses organise, read and report on bundles of medical records for clinical negligence litigation. An expert uploads an unsorted PDF bundle (typically 1,000–4,000 pages). The system:

1. Numbers and virus-scans every page, then stores the bundle in encrypted cloud storage.
2. Reads each page with optical character recognition (OCR) to extract the text.
3. Strips personally identifiable information from the text, then uses an AI large language model to extract structured facts (dates, consultations, prescriptions, results) — each linked to the exact words on the page.
4. Presents the pages to the expert, who reads every page, checks every AI suggestion, and verifies or corrects each fact.
5. Assembles the expert's verified facts and written opinions into a court-compliant report, which the expert signs electronically.
6. Supports the post-report stage: answering written questions, joint expert meetings, re-paginated trial bundles, and a witness-box lookup tool.
7. Deletes all data at the end of the retention period using cryptographic shredding, and issues a certificate of deletion.

### Scope of the processing

**Personal data processed about patients (special-category):**

- Medical records: page images, OCR text, handwritten notes
- Extracted clinical facts: consultations, test results, prescriptions, referrals — each with exact page and word references
- Patient identity: name, other names, date of birth (sealed with per-case envelope encryption)
- Patient identifier hash (for duplicate-case detection only; never stored in the clear)
- Expert's dictated notes and written opinions about the patient's care

**Personal data processed about expert witnesses:**

- Full name, email, locale, timezone, home region
- Professional identity: profession, specialties, registration body and number, hourly rate, qualifications
- Signature image and declarations
- Session data: device label, sign-in/out times, MFA status
- Billing details: legal name, address, tax number, bank details

**Personal data about third parties:**

- Case party names (e.g. hospital trusts, solicitor firms)
- Instructing party names, firms, emails, references
- Colleague spot-check: email and watermarked image crops only
- Expert meeting participants: name, discipline, side

### Context of the processing

The processing takes place in the context of civil litigation in England and Wales. The expert witness is instructed by solicitors acting for one party (typically the claimant) in a clinical negligence claim. The expert must review the patient's complete medical records, identify relevant facts, and produce a report that meets the requirements of Part 35 of the Civil Procedure Rules. Avodeck assists but does not replace the expert's judgement: the AI never writes opinion, and every fact must be verified by the expert before it can appear in a report.

### Purpose of the processing

1. Organising and paginating an unsorted bundle of medical records so every page has a stable reference number.
2. Extracting structured facts from handwritten and typed records, with provenance to exact words, reducing the risk of the expert missing a relevant entry.
3. Enabling the expert to read, verify and annotate every page efficiently.
4. Assembling a court-compliant report from the expert's own verified facts and opinions.
5. Supporting post-report litigation stages (questions, meetings, trial bundles, the witness box).

---

## 2. Necessity and proportionality

### Lawful basis (Article 6)

**Article 6(1)(f) — legitimate interests** of the data controller (the expert witness or their organisation). The expert has a legitimate professional interest in efficiently reviewing medical records to fulfil their duty to the court. Processing through Avodeck is necessary because bundles of 1,000–4,000 pages are impractical to organise manually, and missing a relevant entry could harm the patient's legal claim or mislead the court.

### Condition for processing special-category data (Article 9)

**Article 9(2)(f) — processing necessary for the establishment, exercise or defence of legal claims.** The medical records are processed solely in connection with ongoing or anticipated litigation. This is the standard lawful basis for litigation support handling medical records in England and Wales.

### Data minimisation

- Personally identifiable information is stripped by Presidio (with pack-specific recognisers) before any text is sent to the AI model. Identifiers on page images are painted black before any vision-model call.
- The AI model receives de-identified text only. It has no tools, no network access, and no access to other cases.
- AI output is structured data (dates, codes, short labels) — never free-prose opinion or rewritten clinical notes.
- Only the specific OCR words cited by the AI are stored as fact values; surrounding context is not retained beyond the session.
- Prompts, AI inputs and AI outputs are not stored. Only metadata is logged (task type, model ID, region, token count).
- Patient name and date of birth are sealed with a separate per-case encryption key, accessible only to authorised users.

### Proportionality

The alternative — manual review without AI assistance — is the current practice. It is slower, more error-prone (experts commonly miss entries in large bundles), and more expensive for the patient's legal claim. Avodeck's AI assistance is proportionate because: (a) it processes only what is necessary (de-identified text), (b) it never replaces the expert's judgement, (c) every AI suggestion must be verified by the expert, and (d) the data is encrypted, access-controlled and deleted when no longer needed.

---

## 3. Consultation

- **Expert witness users:** [To be completed — record of user research and feedback on data handling]
- **Data protection adviser:** [To be completed — name of solicitor or DPO who reviewed this DPIA]
- **Technical review:** Architecture, threat model and security measures reviewed during development. Security decisions documented in Architecture Decision Records (ADRs 0010–0032).

---

## 4. Data flows

1. **Upload:** Expert uploads PDF bundle via browser → FastAPI server. File is chunked, SHA-256 verified, virus-scanned (ClamAV, fail-closed). Stored in AWS S3 London (eu-west-2) with SSE-S3 encryption plus per-case AES-256-GCM envelope encryption (key in AWS KMS London).
2. **OCR:** Worker retrieves encrypted pages → decrypts in memory → sends page images to Azure AI Document Intelligence (UK South, data logging disabled) → receives text and layout → encrypts and stores in Supabase Postgres (London).
3. **AI extraction:** Worker takes OCR text → Presidio strips PII (names, NHS numbers, DOBs, addresses) → de-identified text sent to Claude via AWS Bedrock (London, eu-west-2, invocation logging disabled, zero data retention) → structured facts returned → server validates every value appears in cited OCR words → stores encrypted facts.
4. **Expert review:** Expert reads each page in browser (server enforces blind-read: no AI suggestions shown until page is marked as read). Expert verifies, corrects or rejects each fact.
5. **Report:** Server assembles report from verified facts and expert's written opinions → expert signs with step-up MFA → report locked (immutable once served).
6. **Deletion:** After retention period expires → daily job destroys per-case encryption key (crypto-shredding) → purges all database rows → issues deletion certificate with counts, hashes and backup expiry date. Deletion ledger replayed after backup restore.

> **Compute transit:** The API and worker run on Railway (Amsterdam). No patient data is written to disk or persisted at rest — data exists in container memory only during active processing. The Netherlands has EEA adequacy. All patient data at rest is in UK data centres only.

---

## 5. Sub-processors

| Provider | Location | Patient data? | Purpose |
|---|---|---|---|
| Supabase | London | Yes (encrypted) | Database, authentication, job queue |
| AWS S3 | eu-west-2 London | Yes (encrypted) | Case file storage |
| AWS KMS | eu-west-2 London | Keys only | Envelope key management |
| AWS Bedrock (Claude) | eu-west-2 London | De-identified text only | AI fact extraction (no training, no retention) |
| Azure Document Intelligence | UK South | Yes (page images) | OCR (data logging disabled) |
| Azure Speech | UK South | Audio only (transient) | Dictation relay |
| Railway | Amsterdam | Transit only, no data at rest | API and worker compute |
| Vercel | Global CDN | No | Static frontend files |
| Resend | US | No | Transactional email (patient-free content only) |
| Sentry | EU | No | Error monitoring (scrubbed, no patient text) |
| PostHog | — | No | Analytics (event allow-list, no free text) |

---

## 6. Risks to individuals

| Risk | Likelihood | Severity | Overall |
|---|---|---|---|
| Unauthorised access to medical records | Low | High | **Medium** |
| Patient data processed or stored outside UK | Very low | Medium | **Low** |
| AI produces incorrect facts used in court evidence | Medium | High | **Medium** |
| Re-identification of patient from de-identified AI input | Very low | High | **Low** |
| Loss or theft of device with offline witness-box package | Low | High | **Medium** |
| Sub-processor staff access to patient data (platform-level) | Very low | High | **Low** |
| Data retained beyond necessity | Low | Medium | **Low** |
| Expert account compromise | Low | High | **Medium** |

---

## 7. Measures to mitigate each risk

### Unauthorised access to medical records

- Per-case AES-256-GCM envelope encryption — even a database breach exposes only ciphertext.
- Every API route calls `require_case_access(action)` with a named permission. Row-level security repeats the check at the database level.
- Access is limited to: the named expert, case members whose role allows the action, or an organisation admin for administrative actions only.
- Append-only, hash-chained audit log of all data access.
- httpOnly, Secure, SameSite=Strict session cookies. CSRF double-submit token.

### Patient data outside UK

- IAM policy denies `global.*` and `eu.*` Bedrock model profiles. Only London-region (eu-west-2) endpoints permitted.
- Guard tests verify no out-of-region endpoint or model ID in the codebase.
- Railway Amsterdam holds data in container memory only (no disk, no persistence). Netherlands has EEA adequacy.
- Email (Resend) and analytics (PostHog) receive no patient data whatsoever.

### AI produces incorrect facts

- The AI never writes opinion (enforced by output schema and guard tests).
- Every extracted value must appear in the cited OCR words — the server rejects values not found in those words.
- Blind-read enforcement: the expert sees and reads every page before any AI suggestion is shown.
- Every fact must be individually verified by the expert before it can be cited in a report.
- The expert can correct or reject any AI suggestion.

### Re-identification from de-identified AI input

- Presidio with pack-specific recognisers strips names, NHS numbers, DOBs, addresses and other identifiers.
- Page images have identifiers painted black before vision-model calls.
- AI provider (AWS Bedrock) contractually commits to zero data retention and no training on input.
- Invocation logging is disabled.

### Device loss with offline package

- Offline witness-box package encrypted with AES-GCM and a PIN-derived key.
- Package auto-expires after a set period.
- Remote revocation available if device is reported lost.

### Sub-processor platform staff access

- Per-case envelope encryption means platform staff see ciphertext only.
- All sub-processors have their own data processing agreements.
- Patient identity (name, DOB) sealed with a separate key layer.

### Data retained beyond necessity

- Retention period configurable per case, driven by pack defaults and organisation policy.
- `delete_after` computed automatically from settlement/withdrawal date + retention period.
- Daily deletion job: crypto-shredding (destroy case key), purge all rows, issue deletion certificate.
- Deletion ledger replayed after backup restore to ensure deleted cases stay deleted.
- Legal holds block deletion; expired holds auto-release.

### Expert account compromise

- Multi-factor authentication available, required at sign-up for organisations.
- Step-up MFA (completed in last 10 minutes) required for sign, send, export, delete and retention changes.
- 30-minute idle timeout, 12-hour maximum session length.
- Support staff cannot access case content without expert's explicit 24-hour grant.

---

## 8. Residual risks accepted

- **Supabase and Railway platform staff** have theoretical access to infrastructure. Mitigated by per-case encryption (they see ciphertext) and contractual obligations, but the risk cannot be eliminated entirely while using managed infrastructure.
- **AI model provider** processes de-identified text in memory during inference. Contractual terms prohibit retention and training, but we rely on the provider honouring those terms.
- **Expert's own device security** is outside Avodeck's control. MFA and session controls reduce but do not eliminate the risk of a compromised device.
- **Plaintext in container memory** during active processing on Railway. Railway's own disk encryption applies, but data in active memory is theoretically accessible to a sufficiently privileged attacker.

---

## 9. Automated decision-making

Avodeck uses AI to suggest structured facts extracted from medical records. However, this does **not** constitute automated decision-making with legal or similarly significant effects under Article 22 UK GDPR (as replaced by the Data (Use and Access) Act 2025 from 5 February 2026), because:

- The AI produces suggestions only — it never writes opinion, conclusions or recommendations about the patient's care.
- Every AI suggestion must be individually verified or rejected by the expert witness before it has any effect.
- The expert makes all clinical and legal judgements. The AI assists with organisation, not decision-making.
- No decision about any individual is made solely on the basis of automated processing.

---

## 10. Prior consultation (Article 36)

Article 36 UK GDPR requires the controller to consult the ICO before processing if the DPIA indicates that the processing would result in a high risk that cannot be mitigated. Having applied the measures in section 7, all residual risks in section 8 are assessed as low or medium. Prior consultation is therefore not required at this time. This assessment should be revisited if a new risk is identified or if the processing changes materially.

---

## 11. Review and sign-off

This DPIA must be reviewed and updated:

- At least annually
- When the processing changes materially (new data types, new sub-processors, new AI capabilities)
- When new risks are identified
- Before expanding to new jurisdictions or case types

| | |
|---|---|
| **Prepared by** | Dr Aishat Abubakar, Director, Four Integers Ltd |
| **Date** | |
| **Reviewed by** | [Data protection adviser name] |
| **Date** | |
