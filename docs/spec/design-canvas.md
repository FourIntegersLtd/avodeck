# Design canvas

All 33 screens and the design system: https://claude.ai/artifact/5MC899UqoP9mjRted5c1rK (private; the master copy).

**Claude Code: use the copy in the repo.** Each board below is in `docs/design/images/<File>.png` (picture) and `docs/design/screens/<File>.dc.html` (exact wording and styles). See `docs/design/README.md`.

| No. | Screen | File | Slice |
|---|---|---|---|
| — | Design system | Main.dc.html | Foundation |
| 01 | Sign in | S01-SignIn | Foundation |
| 02 | Your expert profile | S02-Profile | C |
| 03 | Today | S03-Today | Foundation |
| 04 | New case | S04-NewCase | C |
| 05 | Before you start | S05-Conflicts | C |
| 06 | What you've been asked | S06-Questions | C |
| 07 | Preparing your bundle | S07-Preparing | A |
| 08 | Your bundle is ready | S08-BundleReady | A |
| 09 | Ask the solicitor for what's missing | S08b-AskSolicitor | A |
| 10 | Reading | S09-Reader | B |
| 11 | Pages we couldn't read | S10-CouldntRead | B |
| 12 | The timeline | S11-Timeline | D |
| 13 | One consultation, as the GP saw it | S12-Consultation | D |
| 14 | Things to look at | S13-ThingsToLook | D |
| 15 | Points against | S14-PointsAgainst | E |
| 16 | Your opinion | S15-Opinion | E |
| 17 | Your report | S16-Report | F |
| 18 | Final check | S17-FinalCheck | F |
| 19 | Sign and send | S18-SignSend | F |
| 20 | Time and invoice | S18b-Invoice | F |
| 21 | After serving | S19-Served | G |
| 22 | Questions from the other side | S20-Questions35 | G |
| 23 | New records and the other expert's report | S21-NewMaterial | G |
| 24 | Correct or update your report | S21b-Addendum | G |
| 25 | Joint statement | S22-JointStatement | I |
| 26 | Trial page numbers | S23-TrialPages | I |
| 27 | Trial pack | S24-TrialPack | I |
| 28 | In the witness box | S25-Lookup | I |
| 29 | Close the case (with pause deletion) | S26-Close | H |
| 30 | Not this patient's record | S30-NotThisPatient | A |
| 31 | Court orders and hearings | S29-CourtOrders | G |
| 32 | Organisation and members | S27-OrgMembers | O |
| 33 | Account and security (devices, backup codes, messages, custodian, close account) | S28-Account | Foundation (account), O (organisations, closure) |

**The file code is the master numbering** (S01–S30, with 08b, 18b, 21b): code, tests, briefs and the architecture use it. The canvas board numbers (01–33) are display order only.

**Note:** the canvas shows the launch packs' labels (England and Wales, general practice): "solicitor", "GP", "joint statement", "trial bundle". In code these are pack keys (`instructing_party`, `clinician`, `expert_meeting_document`, `court_bundle`); never copy the canvas words into components.

**Behaviour changes shown on existing boards (October 2026):** sign-in backup code (01); registration body and multiple specialties, link to account (02); organisation filter, badges and messages bell on Today (03); "Who is this case for?", joint instructing parties, parties to the claim, patient status (04); conflict check across all organisations (05); instruction versions (06); exhibits and stop preparing (07); possible wrong-patient pages, exhibits, non-English page, later deliveries (08); note on set-aside pages (08b); turn page, clearer copy, not this patient (09, 10); earlier versions of an opinion (15); "no fact changed since cited" check (17); recipients and supplementary report wording (18); issuer, payment status and credit notes (18b); court orders card and trial dates (19); rounds of questions (20); deliveries (21); participants, agenda versions and signed-elsewhere upload (22); court bundle versions (23); trial pack built per hearing (24); pause deletion (26).
