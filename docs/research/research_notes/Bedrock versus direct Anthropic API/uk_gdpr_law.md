# UK GDPR / DPA 2018 requirements when a UK SaaS processor sends identifiable medical records to Anthropic's direct API (US/global inference), for medico-legal work (E&W), position at 2 Nov 2026

Method note: primary sources were fetched in this session (ICO, legislation.gov.uk, gov.uk, Anthropic DPA, NHS England). Several fetches were summarised by a small model, so quotations are as returned by the tool; verify wording before quoting in a legal document. Items I could not verify are marked UNVERIFIED and listed under Gaps. This is research, not legal advice.

## 1. Article 9: special category status, Article 9(2)(f), and who is controller vs processor

### Takeaway
Medical records are health data and therefore special category: an Article 6 basis AND an Article 9 condition are both needed, and the ICO treats 9(2)(f) (legal claims) as covering actual or prospective litigation and obtaining legal advice, subject to necessity and proportionality. Who the controller is (instructing solicitor, the expert, or both as independent controllers) is NOT settled by any authoritative E&W source I found; it must be set by the contract chain, and it determines who must authorise Avodeck's sub-processors.

### Cited Findings
- ICO: to process special category data you must meet an Article 9 condition, and "you must always identify an article 6 lawful basis"; the Article 9 conditions do not replace the Article 6 basis. The ICO lists ten Article 9 conditions, including "(f) Legal claims or judicial acts", and its table shows (f) needs no separate DPA 2018 Schedule 1 condition. — [ICO: What are the rules on special category data](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/lawful-basis/special-category-data/what-are-the-rules-on-special-category-data/)
- ICO on 9(2)(f): "'Legal claims' in this context isn't limited to current legal proceedings"; covers actual or prospective court proceedings, obtaining legal advice, and establishing/exercising/defending legal rights; the data must be "relevant and proportionate" and you must show the processing is necessary for the claim. The ICO's worked examples are claims against the controller itself (employer, hairdresser) and a trust adviser establishing a beneficiary's rights; the page does not address third-party claims directly. — [ICO: What are the conditions for processing](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/lawful-basis/special-category-data/what-are-the-conditions-for-processing/)
- Burges Salmon reads the ICO guidance as confirming 9(2)(f) covers processing needed to obtain legal advice (secondary source). — [Burges Salmon](https://burges-salmon.com/news-and-insight/legal-updates/data-protection/the-gdpr-special-category-data-in-the-context-of-legal-advice-and-trusts)
- ICO: the controller is responsible for assessing that its processor is competent to process in line with UK GDPR; processors have direct obligations of their own. — [ICO controllers and processors guide](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/controllers-and-processors/controllers-and-processors-a-guide/)
- Controller/processor status of instructed experts is contested in practice. UK Register of Expert Witnesses newsletters (2018) said experts given data by instructing solicitors act as processors, while later notes say some experts see themselves as independent of instructing parties and that it is for each expert to decide per case. — [JS Publications YW91](https://www.jspubs.com/experts/yw/YW91.pdf), [YW92](https://www.jspubs.com/experts/yw/YW92.pdf)
- A Local Government Lawyer article (E&W, secondary) says experts are independent data controllers and that solicitors can use a template instruction to confirm this. — [Local Government Lawyer](https://localgovernmentlawyer.co.uk/litigation-and-enforcement/311-litigation-features/38784-gdpr-and-civil-claims)
- Law Society of Ireland (Sept 2026, Irish not E&W): solicitors and barristers are generally independent controllers, a barrister may be a processor for the solicitor; same considerations apply to instructed experts. — [Law Society of Ireland Gazette](https://www.lawsociety.ie/gazette/top-stories/2026/september/solicitors-accountable-for-data-use-under-gdpr/)
- Law Society of Scotland submission: medical records are special category, and sharing them with an expert needs a legal basis plus an additional condition. — [Law Society of Scotland](https://www.lawscot.org.uk/media/02vfg0k0/submissions-on-behalf-of-the-law-society-of-scotland-gdpr-and-solicitors.pdf)
- The RCS expert witness guide states the expert's duty to the court overrides obligations to those instructing or paying them (relevant to independence arguments for controller status). — [RCS Expert Witness Good Practice Guide 2019](https://rcseng.ac.uk/-/media/files/rcs/standards-and-research/standards-and-policy/good-practice-guides/2019/rcs-expert-witness-good-practice-guide-2019.pdf)

### Inferences
- Health data is special category under UK GDPR Art 9(1)/Art 4(15) (I did not fetch the legislation text this session; the ICO pages above treat medical records as special category). Treat as certain.
- 9(2)(f) is very likely available to the instructing solicitor/expert for a live or prospective clinical negligence claim, but it is a controller-side condition tied to necessity. Avodeck, as processor, does not need its own Article 9 condition; it processes on the controller's documented instructions. Avodeck should not describe 9(2)(f) as something it "relies on".
- Necessity/proportionality is the real exposure for a direct-API design: the controller must be able to say why sending page text to a US-processed LLM is necessary, which is a question of DPIA and minimisation rather than of 9(2)(f) itself.
- Likely structure: expert (or instructing solicitor) = controller; Avodeck = processor; Anthropic = sub-processor (Module Three). If the expert is an independent controller, the expert authorises sub-processors; if the solicitor is controller and the expert a processor, the authorisation chain is longer. Because this is contested, Avodeck's terms should state the role it assumes and make the customer warrant it.

### Gaps
- No authoritative E&W source (ICO, SRA, Law Society E&W, CJC, GMC) found that settles expert-witness controller/processor status. The ICO fetch of the roles page returned 404 so I could not quote its test text.
- Exact legislation text of Art 9(2)(f) and Art 4(15) not fetched this session (UNVERIFIED from the primary text; ICO restatement used).
- DPA 2018 Sch 2 Part 3 para 5 (legal-proceedings disclosure exemption) not checked: UNVERIFIED.

## 2. Does partial pseudonymisation (identifiers stripped) take data out of scope?

### Takeaway
No. ICO guidance states pseudonymised data is still personal data and warns against confusing it with anonymisation; stripping names, NHS numbers and dates of birth from clinical free text leaves indirectly identifiable data, and the ICO's anonymisation test is whether identification is "reasonably likely" (motivated intruder), which free-text medical records will very rarely meet. The controller still holds the key (the originals), so the data is personal data at least in Avodeck's hands.

### Cited Findings
- ICO pseudonymisation guidance: "Pseudonymised data is personal data in the hands of someone who holds the additional information"; when direct identifiers are stripped, people are "indirectly identifiable using additional information or other techniques"; "Take care not to confuse pseudonymisation with anonymisation"; pseudonymisation "is not a way of transforming personal data to the extent the law no longer applies". The page carries a banner that it is under review because of the Data (Use and Access) Act. — [ICO pseudonymisation](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/data-sharing/anonymisation/pseudonymisation/)
- ICO anonymisation guidance published 28 March 2025; describes the "spectrum of identifiability" and the "reasonably likely"/"motivated intruder" tests; marked "under review and may be subject to change" after the DUAA; not a statutory code. — [ICO anonymisation: about this guidance](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/data-sharing/anonymisation/about-this-guidance/)

### Inferences
- Page text after Presidio still contains dates of consultations, places, rare conditions, family details, GP and hospital names, addresses not caught by the recogniser, etc. It should be treated as personal and special category data for every legal analysis (transfer, Article 28, DPIA, confidentiality).
- Whether the *recipient* (Anthropic) could re-identify is the relative-identifiability argument. I found no ICO statement that would let Avodeck rely on it for free-text medical records; do not build the compliance case on it. (The DUAA added a statutory test of identifiability from the viewpoint of the controller/processor and those it is likely to share with: section 1 DUAA, UNVERIFIED, not fetched.)
- Stripping identifiers is still valuable: it is a Art 32 / minimisation measure and strengthens the TRA (lower risk), but it does not change the legal classification.

### Gaps
- The ICO anonymisation guidance's "identifiability" page was not fetched, so the exact motivated-intruder wording is not quoted.
- I did not verify the DUAA's identifiability provision.

## 3. International transfers (Chapter V): UK residency, DPF / UK Extension, IDTA / Addendum, TRA, and DUAA changes

### Takeaway
UK GDPR does NOT require data to stay in the UK; it requires a Chapter V mechanism for a restricted transfer. For a direct-API route the options are (a) the UK–US data bridge if Anthropic is an active UK Extension participant covering this data type (I could NOT verify that), or (b) IDTA or the ICO Addendum plus a documented transfer risk assessment. Anthropic's current published DPA already incorporates the EU SCCs (Modules Two/Three) and the ICO UK Addendum and does not mention the DPF. The DUAA "not materially lower" test has been in force since 5 Feb 2026.

### Cited Findings
- ICO: UK GDPR does not require data to stay in the UK; a restricted transfer needs one of three routes: adequacy regulations, Article 46 appropriate safeguards (IDTA, Addendum, BCRs) plus a TRA, or Article 49 exceptions. The rules attach to whoever initiates the transfer, "whether controller, processor or sub-processor". Guide published and last updated 15 Jan 2026. — [ICO international transfers guide](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/international-transfers/international-transfers-a-guide/)
- ICO on a TRA: confirms the standard of protection after transfer is "not materially lower"; if the TRA finds it is not, the safeguard cannot be relied on alone. — same ICO guide.
- UK–US data bridge: The Data Protection (Adequacy) (United States of America) Regulations 2023, in force 12 Oct 2023 (laid 21 Sept 2023); covers transfers to US persons who participate in the UK Extension to the EU–US DPF, with no further authorisation needed. — [gov.uk data bridge documents](https://www.gov.uk/government/publications/uk-us-data-bridge-supporting-documents)
- ICO (page updated 30 July 2026): check the recipient on the DPF list, that certification is active and covers the data type; the UK Extension requires the US business also to be in the EU–US DPF; if the recipient is not listed, inactive, or not registered for the data type, you must use an appropriate safeguard (with a TRA) or an exception first. For special-category-type data (the ICO page names genetic, biometric ID, sexual orientation, criminal offence data) you must flag it as sensitive to the US recipient and the ICO recommends permanent classification marking; the page as summarised does not list health data in that sentence. — [ICO: How does the UK Extension work](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/international-transfers/adequacy-regulations/how-does-the-uk-extension-to-the-eu-us-data-privacy-framework-work)
- Anthropic DPA (current page): "Customer is the controller and Anthropic is Customer's processor"; incorporates SCC Module Two (controller to processor) or Module Three (processor to processor); SCC clause 17/18 Republic of Ireland; incorporates the ICO UK Addendum version B.1.0 issued under s.119A DPA 2018; precedence SCCs > DPA > Agreement; Anthropic "provides reasonable support" for transfer compliance. The fetched DPA text does not mention the EU–US Data Privacy Framework at all. — [Anthropic DPA](https://www.anthropic.com/legal/data-processing-addendum)
- DUAA 2025 Schedule 7 inserts Article 45B: data protection test met if protection for data subjects is "not materially lower" than under UK law; Article 46(1A): for safeguards-based transfers the exporter must consider, "acting reasonably and proportionately", that the test is met, judged by all circumstances including the nature and volume of the data. — [legislation.gov.uk DUAA 2025 Sch 7](https://www.legislation.gov.uk/ukpga/2025/18/schedule/7)
- Commencement: the Data (Use and Access) Act 2025 (Commencement No. 6 and Transitional and Saving Provisions) Regulations 2026, SI 2026/82 (C.10), bring in most of Part 5, including the new transfers test, on 5 Feb 2026 (secondary summary); the ICO updated its transfers guidance 15 Jan 2026. — [Burges Salmon](https://www.burges-salmon.com/articles/102mh1d/the-data-use-and-access-act-2025-key-implications-for-businesses-as-the-new-in/), [SI 2026/82 contents](https://www.legislation.gov.uk/uksi/2026/82/contents/made) (page confirms regs for 5 Feb and 19 Jun 2026 dates but did not itemise Schedule 7)
- Superseded: pre-2026 commentary describing the "essentially equivalent" test and a TRA that existed only because of Schrems II is superseded by the DUAA wording. — [Burges Salmon](https://www.burges-salmon.com/articles/102mh1d/the-data-use-and-access-act-2025-key-implications-for-businesses-as-the-new-in/)

### Inferences
- A direct-API route with US or "global" inference is a restricted transfer by Avodeck (a UK processor initiating the sending to Anthropic) regardless of the data being pseudonymised, because it is still personal data (section 2). Lawful if a Chapter V route is in place; no UK law prohibits it per se.
- Practical route: the Anthropic DPA already gives SCC + UK Addendum (the Addendum is an Art 46 safeguard), so Avodeck needs (i) that contract executed with the Anthropic entity that signs the Agreement, (ii) a documented TRA under the 2026 "reasonable and proportionate / not materially lower" standard (the volume and sensitivity of a 4,000-page health bundle weigh against, mitigated by stripping, retention terms and no-training commitments), and (iii) confirmation of the Addendum parties/Tables completed for the Avodeck–Anthropic relationship.
- If Anthropic is in fact certified for the UK Extension (UNVERIFIED), the bridge would remove the TRA need for that transfer but not Article 28, DPIA or minimisation duties, and the data would need to be certified for non-HR data.
- Because the DPA's Table 4 says Anthropic "may end the Approved Addendum" (as returned by the fetch), check what happens on ICO Addendum revision.
- Note that Bedrock London is not automatically free of transfer issues (a US-parent provider's remote access is a separate analysis, not researched here).

### Gaps
- UNVERIFIED: whether "Anthropic, PBC" appears on the Data Privacy Framework List with the EU–US DPF and UK Extension. The official site (dataprivacyframework.gov/list) is a JavaScript app that returned no searchable content to my fetch tool, and two web searches returned no Anthropic entry. Someone must search "Anthropic" on https://www.dataprivacyframework.gov/list manually and screenshot status, covered data (HR / non-HR) and expiry.
- Anthropic's sub-processor list (trust.anthropic.com/subprocessors) did not render; so Anthropic's own sub-processors/locations were not verified. Whether the API offers UK/EU-only inference was out of scope.
- I did not fetch the ICO TRA tool/template pages or the IDTA/Addendum pages directly.
- SI 2026/82 explanatory note not read, so the exact commencement of Schedule 7 relies on secondary sources (Burges Salmon; A&O Shearman and Digital Policy Alert surfaced in search with the same date).

## 4. Article 28: processor and sub-processor obligations

### Takeaway
Avodeck cannot add Anthropic as a sub-processor without the controller's prior specific or general written authorisation, must notify changes if the authorisation is general, and must flow down the same data protection terms, remaining fully liable. Anthropic's DPA is built for this (general authorisation to listed sub-processors, 15-day objection window), but the customer (expert/solicitor) must have authorised Avodeck's own sub-processor list, which changing from Bedrock to Anthropic would alter.

### Cited Findings
- UK GDPR Art 28(2): processor shall not engage another processor without prior specific or general written authorisation; under general authorisation the processor must inform the controller of intended changes, giving the controller the opportunity to object. Art 28(3)(a): processes only on documented instructions, including as regards international transfers; (d) complies with the sub-processing conditions; (h) provides information and allows audits. Art 28(4): the same data protection obligations must be imposed on the sub-processor by contract, and the initial processor "shall remain fully liable to the controller". — [UK GDPR Art 28](https://www.legislation.gov.uk/eur/2016/679/article/28)
- Anthropic DPA: general authorisation to sub-processors in Schedule 4 (hosted at anthropic.com/subprocessors); Anthropic gives "reasonable notice" before a new sub-processor has access; the customer may object within 15 days or is "deemed to consent"; sub-processor terms "substantially as protective as" Anthropic's DPA obligations (note: Art 28(4) requires the *same* obligations, an arguable gap between "substantially as protective" and "same" is for counsel). — [Anthropic DPA](https://www.anthropic.com/legal/data-processing-addendum)
- ICO: controller is responsible for checking processor competence. — [ICO guide](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/controllers-and-processors/controllers-and-processors-a-guide/)

### Inferences
- Required by law: written authorisation of Anthropic as a sub-processor (specific, or general via a published list with change notice); Art 28(3) contract with Anthropic that mirrors Avodeck's customer contract; transfer instructions documented (Art 28(3)(a)).
- A customer preference, not a legal rule: any customer clause saying "UK-only" or "no US processing". That is contractual, and Avodeck would breach the contract, not UK GDPR, by sending data to the US against it. Moving from Bedrock London to a US-inference sub-processor is a material change that should go through the notice-and-objection mechanism in Avodeck's own customer terms.
- Because Avodeck's 15-day objection pass-through must fit Anthropic's 15-day window, customer notice periods shorter than Anthropic's could leave Avodeck unable to honour objections.

### Gaps
- Anthropic's current sub-processor list and any UK/EU inference-region commitments were not retrievable.
- Liability caps/indemnities in Anthropic Commercial Terms not reviewed.

## 5. ICO guidance on AI and health data; DPIAs; enforcement

### Takeaway
A DPIA is effectively mandatory for this processing: Art 35(3)(b) names large-scale special category processing, and the ICO also lists novel applications of technology including AI as a trigger when combined with other criteria (sensitive data counts). I found no ICO enforcement action or statement specifically about sending health data to US AI providers.

### Cited Findings
- ICO DPIA guidance: a DPIA is always required for "processing on a large scale of special categories of data" (Art 35(3)(b)); ICO's Art 35(4) list includes innovative technology ("novel application of existing technologies (including AI)") when combined with another criterion; also data matching and risk of physical harm from a breach. The page says it is under review following the DUAA. — [ICO: When do we need to do a DPIA](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/accountability-and-governance/data-protection-impact-assessments-dpias/when-do-we-need-to-do-a-dpia/)
- ICO generative-AI consultation series / eight questions include whether a DPIA has been prepared before personal data is processed in a generative AI system (secondary summary). — [Hunton](https://www.hunton.com/privacy-and-cybersecurity-law-blog/uk-ico-reminds-businesses-of-privacy-risks-in-ai)
- ICO's December 2024 statement on generative-AI model training expects a robust DPIA (secondary summary; about training, not API inference). — [RPC](https://www.rpclegal.com/snapshots/data-protection/winter-2024/ico-statement-on-generative-ai-model-training/)

### Inferences
- "Large scale" is a judgement (volume of data subjects, volume of data, duration, geography). 1,000–4,000 pages of one claimant's records is large in volume but small in number of data subjects per case; across a SaaS customer base it is likely large scale. Either way the AI trigger plus special category data makes a DPIA the expected approach. Who must do it: the controller; Avodeck as processor must assist (Art 28(3)(f)). Avodeck doing its own and offering it is good practice.
- The absence of enforcement is not evidence of lawfulness; I only did one targeted search.

### Gaps
- ICO's own AI and data protection guidance pages and any 2025–26 ICO AI/health statement were not fetched. I found no ICO enforcement on US AI transfers of health data (searched once; absence is weak evidence).
- No check on ICO's DUAA-driven guidance updates (e.g. recognised legitimate interests) that may affect this.

## 6. Common law duty of confidentiality

### Takeaway
The duty is separate from and additional to UK GDPR: disclosure of identifiable patient information outside the care team needs consent, a legal basis in law (such as a court order or statute) or an overriding public interest. Whether anonymisation-by-stripping satisfies it is a question I could not source for E&W; I found only a Northern Ireland departmental summary.

### Cited Findings
- Northern Ireland DoH summary (persuasive only, not E&W): under common law a provider wishing to disclose personal information to anyone outside the care team should first seek the patient's consent; unauthorised disclosure may expose organisation and individual to legal action; arguably applies after death. — [Health NI: common law duty of confidentiality](https://www.health-ni.gov.uk/articles/common-law-duty-confidentiality)
- RCS guide: the expert's duty to the court overrides obligations to those instructing them. — [RCS](https://rcseng.ac.uk/-/media/files/rcs/standards-and-research/standards-and-policy/good-practice-guides/2019/rcs-expert-witness-good-practice-guide-2019.pdf)

### Inferences
- In clinical negligence the claimant normally consents (or is ordered) to disclosure of records to their legal team and the expert; that consent covers the litigation purpose. Whether it covers onward disclosure to a US AI sub-processor is an open question: the Law Society of Scotland submission flags the risk of unauthorised onward sharing of medical records. Good practice: the claimant-facing consent/instruction terms and the expert's terms should say the records are processed by a technology provider including AI services abroad, and the confidential recipients are bound by contract (Anthropic DPA confidentiality is not reviewed here). I cannot say this is legally required.
- Professional regulators (GMC confidentiality guidance, solicitors' SRA duties) add professional conduct obligations on the expert and solicitor, which a customer may translate into a contractual UK-only requirement. Not checked this session.

### Gaps
- No E&W primary authority (cases such as W v Egdell, Source Informatics) or GMC/Caldicott guidance was fetched; UNVERIFIED for E&W. Courts' CPR disclosure rules (CPR 31, pre-action protocol for medical records) were not reviewed.

## 7. Any UK statute requiring health data to be processed only in the UK; NHS offshoring policy

### Takeaway
I found no UK statute or regulation that requires health data to be processed only in the UK; the UK GDPR is a mechanism-based regime. The "data must only be hosted within the UK" rule is NHS England / DHSC guidance for NHS and social care organisations (with an exception route), not general law, and it binds an expert witness or private SaaS only through contract or customer procurement requirements.

### Cited Findings
- NHS England page "NHS and social care data: off-shoring and the use of public cloud services" (page shows last edited 28 May 2025; original date not stated; issued by NHS England, DHSC and NHS Improvement): "Data must only be hosted within the UK" as the default; hosting in the EEA, in adequate countries, or in the US under an International Data Transfer Agreement can be relied on only if transfer risks are sufficiently low, with SIRO and executive approval and an Article 46 risk assessment; applies to health and social care organisations (controllers, IAOs, SIROs, DPOs, Caldicott Guardians); the cloud risk framework is described as guidance. — [NHS England / NHS Digital offshoring page](https://digital.nhs.uk/data-and-information/looking-after-information/data-security-and-information-governance/nhs-and-social-care-data-off-shoring-and-the-use-of-public-cloud-services)
- Older 2018 coverage reports the original guidance allowed EEA or Privacy Shield US providers; Privacy Shield has since been invalidated, so those summaries are superseded. — [DatacenterDynamics 2018](https://www.datacenterdynamics.com/news/nhs-guidelines-authorize-use-of-public-cloud-to-store-patient-data/)

### Inferences
- The NHS rule applies to NHS bodies' own data estates. Medico-legal records held by a private expert or law firm are copies provided for litigation; the NHS rule is not directly applicable, but instructing firms (especially NHS Resolution panel solicitors, defendant-side) may impose contractual or panel requirements. NHS Resolution's own terms were not researched: UNVERIFIED.
- Even on NHS's own guidance the US is permissible with approvals and a risk assessment, so "UK-only" is a policy preference/default, not an absolute bar.

### Gaps
- No statute search was exhaustive (e.g., Health and Social Care Act 2012 provisions, DSPT requirements, National Data Guardian guidance, NHS Resolution panel requirements, MoJ/legal aid security requirements). Statement "no statute found" reflects the searches done, not a proven negative.
- The NHS page's date (28 May 2025) comes from a summarised fetch and may relate to wiki edit time, not policy effective date; text still refers to EEA/EC adequacy concepts, suggesting it predates parts of the post-Brexit regime.
