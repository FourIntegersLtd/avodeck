# UK medico-legal AI suppliers: where records go, and what the UK legal/medical sector expects (as of 2 November 2026)

Method note: pages were read through WebFetch, which returns a model-written extract. Quotes below are the extractor's quoted strings and were not re-checked against raw HTML. Where a primary page returned 403/404/500 I say so and mark secondary sourcing. PDFs (CJC final report, Bar Council Nov 2025, judicial guidance Oct 2025) were downloaded and read as text directly.

## 1. Verification of the four company claims

### Takeaway
Instruct.Expert: CONFIRMED. Sortica: CONFIRMED on its own published wording (with caveats; the DPA itself is unseen). Med-Legal: PARTLY (the admission of unverified transfers is real; naming xAI is UNVERIFIED). Kalynto: UNVERIFIED, and the company found at kalynto.com is a New York lending platform, not a medico-legal supplier, so the claim looks misattributed.

### Cited Findings

**Claim 1: Instruct.Expert, OpenAI API, US processing, DPA + UK Addendum, 24-hour deletion. CONFIRMED.**
- Product: it describes an "AI matching engine" for cases/experts, so it handles case descriptions and expert profile data, not full medical-record bundles. — [Instruct.Expert privacy](https://instruct.expert/privacy)
- AI use: "Our platform uses artificial intelligence (specifically OpenAI's GPT-4o-mini model)"; data sent: "Sending the case description and available expert profile data to the OpenAI API for analysis." — [privacy s6](https://instruct.expert/privacy)
- US location and mechanism: "case details are transmitted to OpenAI's API servers, which are located in the United States."; "This transfer is protected by Standard Contractual Clauses (UK Addendum)"; "OpenAI is certified under the UK-US Data Privacy Framework." — [privacy s8](https://instruct.expert/privacy)
- DPA: "OpenAI acts as a data processor and is subject to a data processing agreement that includes Standard Contractual Clauses" — [privacy s7](https://instruct.expert/privacy)
- Deletion: "Case descriptions are automatically deleted within 24 hours of submission."; OpenAI "does not use API inputs to train their models". — [privacy s5/s9](https://instruct.expert/privacy)
- Page date not captured.

**Claim 2: Sortica, EU-only endpoint, UK-adequacy region, zero data retention, Bedrock London fallback, provider named in DPA. CONFIRMED as to what Sortica publishes (DPA itself not seen).**
- Sortica: "UK medical chronology software for clinical negligence and personal injury teams". — [sortica.ai](https://sortica.ai)
- Primary path: "Model calls go through an EU-only endpoint and are refused rather than routed anywhere else."; region label "UK GDPR adequacy"; AI inference "runs in a region covered by UK GDPR adequacy". — [Data protection page](https://sortica.ai/dataprotection)
- ZDR: the page says the provider "permits health data only with zero data retention", so "prompts and outputs are not stored"; "inputs and outputs are not used to train any model". — [Data protection page](https://sortica.ai/dataprotection)
- Fallback: "If the primary AI model provider is unavailable, the same request is processed on Amazon Bedrock instead", "in the same London region as your documents"; credential "permitted to invoke models in eu-west-2 only"; "Search embeddings are always created here." — [Data protection page](https://sortica.ai/dataprotection)
- Provider naming: "We name the provider and confirm the retention position for your firm in the DPA." The public page never names the primary provider or region. — [Data protection page](https://sortica.ai/dataprotection)
- Storage: "Documents, OCR, generated bundles and the application database are stored and processed in AWS eu-west-2 (London)." — [Data protection page](https://sortica.ai/dataprotection)
- Caveats: (a) the privacy page is labelled "Draft — pending solicitor review before publication" and "do not treat the text below as binding"; (b) the privacy page lists "an AI model provider" and "AWS — including Amazon Bedrock as the AI fallback" as sub-processors and does not mention an EU endpoint or ZDR; (c) ZDR is described as something the provider's agreement "permits", not as verified active. — [Sortica privacy](https://sortica.ai/privacy)
- So Sortica's primary inference leaves the UK (to an EU endpoint, relying on UK adequacy for the EU); only the fallback is UK-only.

**Claim 3: Med-Legal, xAI API for summaries/chronologies, possibly outside UK/EU, admits unverified transfer coverage. PARTLY.**
- Admission CONFIRMED. Policy ("Last updated: 11 October 2026"): "We use an external AI provider to turn record text into summaries, chronologies and indexes."; "AI and other service providers may process data outside the UK and EEA, including in the USA."; "We have not yet verified the applicable international-transfer arrangement for our active AI account."; "Verification of our active account's terms and data-sharing settings is still pending." — [med-legal.co.uk/privacy](https://med-legal.co.uk/privacy) (same text at [mirror host](https://167.233.74.35.sslip.io/privacy))
- Naming of xAI: UNVERIFIED. The privacy page and security page as extracted do not name xAI (or any provider). Security page: "AI processing may take place outside the UK and EU." — [Security & data](https://med-legal.co.uk/security). xAI may appear in a document I did not reach (Terms & Fees, DPA on request, an earlier policy version).
- Related: "Live records are stored in Germany, with encrypted recovery copies in the UK."; originals "scheduled for deletion 24 hours after upload"; "Med-Legal does not train its own models on your records."; a DPA is "available on request". No ZDR statement. — [Security & data](https://med-legal.co.uk/security)
- A search snippet said the policy names Hetzner; the fetch said it does not. Unresolved.
- Wording note: the policy says "UK and EEA"; the claim said "UK/EU".

**Claim 4: Kalynto, published DPA lists Anthropic PBC (US) as sub-processor for AI. UNVERIFIED; likely misattributed.**
- kalynto.com is "The lending operating system for UHNW credit", founded in New York; "We are not a lender, broker, or advisor." Not a medico-legal business. — [kalynto.com](https://kalynto.com), [privacy](https://kalynto.com/privacy)
- Privacy page: no mention of Anthropic or sub-processors; AI vendor not named ("processed within the platform's AI pipeline"); no data location stated. — [privacy](https://kalynto.com/privacy)
- Homepage: "Kalynto has Zero Data Retention agreements in place." and "Client data is not used to train AI models." — [kalynto.com](https://kalynto.com)
- /dpa returned HTTP 404. No Kalynto medico-legal product found in two searches.
- Therefore: no published DPA listing Anthropic found; the entity found is not in the UK medico-legal market.

### Inferences
- Only one of four (Instruct.Expert) openly sends data to the US under SCC/DPF, and that is case descriptions, not record text. Med-Legal sends record text to an unnamed provider and says its transfer basis is unverified. Sortica sends record text to an EU endpoint. None of the four claims shows a UK-only primary path.
- Sortica's two-tier design (EU primary with ZDR, Bedrock London as fallback) is evidence a UK supplier chose an EU endpoint over UK-only for primary inference, but its public text asserts UK GDPR adequacy, not a UK-only restriction.

### Gaps
- Sortica's actual DPA and the name of its primary provider (given only to customers). Med-Legal's DPA. Whether Med-Legal ever named xAI. Any Kalynto DPA. Dates of Instruct.Expert and Sortica pages.

## 2. Other UK medical-chronology / medico-legal AI tools

### Takeaway
Few UK suppliers publish where AI inference runs. Of those checked, SideLight (London) shows only "UK Hosted", NHS DSPT and ICO badges and does not name its AI provider; US tools show certifications (SOC 2) but no UK residency.

### Cited Findings
- SideLight: "AI-driven" medicolegal case management for clinical negligence and personal injury, UK headquarters. Homepage badges: "UK Hosted", "NHS DSPT", "ICO Registered", "GDPR Compliant"; no AI provider, sub-processor, ISO 27001 or Cyber Essentials statement on the page. — [sidelight.ai](https://www.sidelight.ai/), [directory entry](https://www.legaltechnologyhub.com/vendors/sidelight/)
- InPractice (medical summaries for law firms): Goleta, California company; page says nothing on UK data location or AI provider. — [inpractice.ai](https://inpractice.ai/blog/medical-summaries-for-law-firms)
- Tavrn: listed with "SOC 2 Type II certified", MFA/SSO, no training on client data; location and AI provider not stated. The same article credits UK data residency to Supio, not Tavrn. — [Tavrn article](https://tavrn.ai/blog/medical-chronology-software)
- Supio: Seattle-based, US personal injury focus, claims SOC 2 Type 2 and GDPR compliance; a search found no UK residency or UK launch evidence. — [Supio](https://www.supio.com/products/medical-chronologies)
- Medico-legal agency Speed Medical advertises ISO 27001 and Cyber Essentials Plus (self-claim). — [Speed Medical](https://www.speedmedical.com/news/posts/how-to-choose-a-medico-legal-agency/)

### Inferences
- Disclosure of AI inference location is rare; it is a differentiator to publish it.

### Gaps
- No UK expert-witness-specific tool found other than Instruct.Expert (matching, not records). SideLight's AI provider and hosting provider unknown. No medical reporting organisation (MRO) found publishing AI processing location. A G-Cloud 15 listing showing UK-only data, ISO 27001, Cyber Essentials Plus and DSPT surfaced in search but could not be attributed to any named vendor, so it is excluded.

## 3. Professional guidance on AI (E&W)

### Takeaway
No source found requires UK-only processing. The consistent expectation is: do not put confidential or personal data into tools you do not control, understand how inputs are used and retained, check vendor terms and where data is processed, stay responsible, and (new, for experts) be ready to declare AI use that shapes the report. The CJC final report (29 October 2026) recommends a CPR change for experts, not yet adopted as far as found.

### Cited Findings
- **Civil Justice Council, final report (published 29 Oct 2026; PDF metadata 28 Oct).** Recommendation Nine: "The CPR should be amended to introduce a requirement for a new, specific declaration concerning AI use where such use generates or influences the substantive content of the expert's report. Where made, the declaration should provide sufficient information, including, where necessary ... disclosure of the AI tool used to the opposing party and the court". Recommendation Ten: the working party revising the CJC's 2014 Guidance for the Instruction of Experts "should introduce good practice guidance on AI use by experts". Existing duty: experts verify reports with a statement of truth (CPR PD 35, para 3.3), which already requires disclosure of methodology. — [CJC final report PDF](https://www.judiciary.uk/wp-content/uploads/2026/10/Use-of-AI-for-the-Preparation-of-Court-Documents-Final-Report.pdf); status page [judiciary.uk CJC](https://www.judiciary.uk/related-offices-and-bodies/advisory-bodies/cjc/current-work/use-of-ai-in-preparing-court-documents/); press [Legal Futures, 30 Oct 2026](https://www.legalfutures.co.uk/latest-news/witnesses-of-fact-and-experts-should-have-to-declare-ai-use-to-court)
- CJC rejects an administrative/substantive split and adopts a "functional, risk-based approach"; declaration targets use that "generates or influences the substantive content". It also notes use of publicly available tools "does raise questions of confidentiality, privilege, and compliance with data protection law", which are "dealt with outside rules of court" as matters of professional regulation and substantive law (said in the translation context, para 7.20). — [CJC final report PDF](https://www.judiciary.uk/wp-content/uploads/2026/10/Use-of-AI-for-the-Preparation-of-Court-Documents-Final-Report.pdf)
- The CJC report cites the Academy of Experts' "Guidance for Expert Witnesses on the use of Artificial Intelligence (AI)" (January 2026). — same PDF, fn 6
- **Judicial AI guidance** (cover date 8 October 2025; PDF metadata 31 Oct 2025): "Do not enter information which is private or confidential. Any information that you input into a public AI chatbot should be seen as being published to all the world." Aimed at public chatbots. On lawyers: "Provided AI is used responsibly, there is no reason why a legal representative ought to refer to its use". — [Judicial guidance PDF](https://www.judiciary.uk/wp-content/uploads/2025/10/Artificial-Intelligence-AI-Guidance-for-Judicial-Office-Holders-1.pdf)
- **Bar Council, "Considerations when using ChatGPT and generative AI software based on LLMs" (Nov 2025, author Samuel Townend KC):** "Be extremely vigilant about sharing with a generative LLM system any legally privileged or confidential information ... or any personal data"; "barristers need fully to understand how the tool they are using operates in this respect, including any relevant protective settings"; "Barristers should check the terms and conditions of any generative LLM system to satisfy themselves that the LLM is compliant with Core Duty 6, rule rC15.5, and any relevant data protection laws." No data-location rule. — [Bar Council PDF](https://www.barcouncilethics.co.uk/wp-content/uploads/2024/01/Considerations-when-using-ChatGPT-and-generative-artificial-intelligence-Nov-2025.pdf)
- **Law Society, "Generative AI: the essentials"** (first published 7 Aug 2024). Page returned 403 to my fetch, so the following is from search-tool summaries of the page, treat as SECONDARY: "generally advisable that you do not feed confidential information into generative AI" where you lack control over the tool; checklist includes reviewing "the generative AI vendor's data management, security and standard" and checking whether the tool is closed or also trains models for third parties; before sharing client data ask "where data is processed, who processes it, how and where it is stored, who can access it". — [Law Society](https://www.lawsociety.org.uk/topics/ai-and-lawtech/generative-ai-the-essentials), [Michelmores via LawCareers](https://www.lawcareers.net/Explore/CommercialQuestion/Michelmores-LLP-Generative-AI-use-and-client-confidentiality)
- **Academy of Experts, AI guidance (Jan 2026):** three tiers, Prohibited / High Risk / Low Risk; high risk includes analysing data, building scenarios, drafting report content or helping form the opinion; low risk includes document organisation, research, spelling; "Experts remain ultimately responsible for their evidence and their duties to the court". Source is a Simmons & Simmons summary dated 24 Feb 2026 (the guidance PDF was not retrievable); that summary did not cover confidentiality or data location. — [Simmons & Simmons](https://www.simmons-simmons.com/en/publications/cmm0r3bjr0010u4gofw4xfdcw/new-guidance-for-expert-witnesses-on-ai), [Academy page](https://academyofexperts.org/knowledge-hub/guidance-for-expert-witnesses-on-the-use-of-artificial-intelligence/)
- **Expert Witness Institute (25 Oct 2024, case update, not formal guidance):** experts should "be extremely careful before entering any confidential or personal information"; AI tools "will generally retain any information you enter"; tell the instructing party early which tool, why and how; ideally raise AI use "in the application for permission to rely on expert evidence, explaining why the tool is needed and how confidential information will be protected"; record agreed AI use in the contract; check insurance for AI exclusions. — [EWI](https://www.ewi.org.uk/News/how-not-to-use-ai-in-expert-evidence)
- **GMC:** the 2008 "Acting as an expert witness" document is marked withdrawn 22 April 2013; if a doctor doubts valid consent for disclosure they should return the information to the instructing party and seek clarification. Current confidentiality guidance in force since 25 April 2017 limits disclosure without consent to legal requirement, public interest, or where seeking consent is not appropriate or practicable, and says identifiable data for non-clinical purposes should be anonymised or consent sought. Both via secondary summaries. — [GMC 2008 doc](https://www.gmc-uk.org/cdn/documents/acting-as-an-expert-witness-2008---2013_pdf-55664193.pdf), [MPS summary](https://www.medicalprotection.org/uk/advice-centre-articles/new-gmc-confidentiality-guidance-what-you-need-to-know)
- **CPR/PD 35:** the only AI-related items found are the CJC recommendations (CPR amendment, revised 2014 Guidance). I found no adopted amendment to CPR Part 35 or PD 35 on AI as of 2 Nov 2026.

### Inferences
- If the CPR is amended as recommended, an expert using Avodeck-type extraction that "influences the substantive content" may need to declare it and, where necessary, name the tool; Avodeck's design (AI does not write opinion, expert verifies) matches the "low-risk/assistive" end, but the CJC "functional" test looks at influence on substance, not label. This is reasoning, not a finding.
- All of this guidance is addressed to lawyers and experts as users; none sets a data-residency rule for suppliers. It makes the supplier's retention, training and sub-processing terms the practical test.

### Gaps
- Primary text of Law Society and Academy guidance on confidentiality/data location (403 / PDF not retrievable). GMC's current expert-witness guidance ("Acting as a witness in legal proceedings", believed to exist) not verified. Whether the Civil Procedure Rule Committee has acted on the CJC recommendations. Bar Council text after Nov 2025 not checked.

## 4. Customer-side expectations (indemnifiers, insurers, law firms, NHS data rules)

### Takeaway
No published supplier requirement on data location or AI use was found from NHS Resolution, MDU, MPS, MDDUS or any named clinical negligence firm. The NHS England offshoring guidance addresses health and social care organisations and is not UK-only; whether it reaches an expert-witness supplier is not established.

### Cited Findings
- NHS England "NHS and social care data: off-shoring and the use of public cloud services" (last edited 28 May 2025): data "must only be hosted within the UK" for the stated lowest-risk class, with EEA, adequate countries, or the US under an IDTA possible if transfer risk is low, with SIRO and EMT approval and a UK GDPR Article 46 assessment. The page is addressed to health and social care organisations (Information Asset Owners, SIROs, Caldicott Guardians), does not distinguish suppliers, does not mention DSPT, and refers to Cyber Essentials. — [NHS England](https://digital.nhs.uk/data-and-information/looking-after-information/data-security-and-information-governance/nhs-and-social-care-data-off-shoring-and-the-use-of-public-cloud-services). Class wording was extractor-summarised; verify before relying.
- DSPT: annual self-assessment for organisations with access to NHS patient data and systems; NHS England's supplier charter says DSPT requirements apply whether or not a supplier signs the charter (via search summary; DSPT "who needs to complete" page returned HTTP 500). — [NHS England supplier letter](https://nhsd-proxy.openprescribing.net/cyber-and-data-security/guidance-and-resources/cyber-security-charter-for-suppliers-to-the-nhs/letter-to-ceos-of-suppliers-to-the-nhs)
- Local NHS practice (secondary): Morecambe Bay primary care collaborative asks AI suppliers to show how the model works, where processing happens at each stage, and what legal process would apply to an access request; article of 3 Sept 2026 argues GP partners bear personal liability for systemic supplier failures. — [Resultsense, 3 Sept 2026](https://www.resultsense.com/news/2026-09-03-nhs-data-residency-gp-liability)
- MDU: warns against putting identifiable patient information into tools like ChatGPT (complaint-response context) and ran a "AI, confidentiality and consent" webinar on 26/30 Mar 2026; nothing on expert witnesses. — [MDU press](https://www.themdu.com/press-centre/press-releases/doctors-warned-by-mdu-about-ai-complaint-responses), [MDU webinar](https://www.themdu.com/guidance-and-advice/podcasts-and-videos/ai-confidentiality-and-consent--26-mar-26)
- NHS Resolution: published item on the national AI Commission report found; no supplier or panel data-location or AI requirements found. — [NHS Resolution](https://resolution.nhs.uk/?p=20689)
- BOA 2024 guidance: duty on the instructing party to supply records to the expert in viewable format; covers secure disposal after the case. — [BOA](https://boa.ac.uk/asset/5E9A9A40%2D160D%2D4C2E%2D9D4D69B326902387)

### Inferences
- The NHS offshoring guidance binds NHS organisations. An expert-witness supplier holding copies of records is not obviously within it, but instructing firms or trusts holding NHS data may flow equivalent expectations down by contract or questionnaire. Not verified for any named firm.
- Even the NHS guidance allows non-UK hosting (EEA, adequate, US with IDTA) subject to approval; "UK-only" is the default preference, not an absolute bar.

### Gaps
- No MPS/MDDUS expert-witness AI guidance; no published supplier policies from NHS Resolution, MDU, MPS, MDDUS, or named claimant/defendant firms (e.g. panel-firm outsourcing questionnaires are private). DSPT scope page unretrieved. The NHS England page's exact Class 1-5 wording needs primary re-check.

## 5. Cyber Essentials / ISO 27001 expectations

### Takeaway
Medico-legal agencies advertise ISO 27001 and Cyber Essentials Plus as trust signals, and NHS supplier guidance points to Cyber Essentials and DSPT; no source found makes either mandatory for an expert-witness software supplier.

### Cited Findings
- Speed Medical: "proud holders of ISO 27001 accreditation and Cyber Essentials Plus" (self-claim). — [Speed Medical](https://www.speedmedical.com/news/posts/how-to-choose-a-medico-legal-agency/)
- CA Stone Medical and Legal data security policy (Jan 2026): shares data only with GDPR-compliant instructing solicitors/agencies and has GDPR contracts with named suppliers; no certification claimed in the extract. — [CA Stone policy](https://www.medicalandlegal.co.uk/wp-content/uploads/2026/01/CA-Stone-Medical-and-Legal-Ltd-data-security-policy-January-2026.pdf)
- SideLight shows an "NHS DSPT" badge but no ISO/Cyber Essentials on the homepage. — [sidelight.ai](https://www.sidelight.ai/)
- NHS offshoring page references NCSC Cyber Essentials; 2018 reporting said suppliers should do annual assessments against ISO standards or Cyber Essentials. — [NHS England](https://digital.nhs.uk/data-and-information/looking-after-information/data-security-and-information-governance/nhs-and-social-care-data-off-shoring-and-the-use-of-public-cloud-services), [Digital Health 2018](https://www.digitalhealth.net/2018/01/nhs-green-lights-cloud-technology/)

### Inferences
- Practical baseline buyers will likely ask for: Cyber Essentials (Plus) and/or ISO 27001, DSPT where NHS data is touched, a signed Art. 28 DPA, sub-processor list with locations, no-training and retention terms. This is an inference from agency marketing, not a published buyer standard.

### Gaps
- No published buyer requirement list from a law firm, indemnifier or NHS Resolution; certification claims above are unaudited.
