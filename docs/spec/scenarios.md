# Scenarios

Real-life situations from start to finish, each written as the behaviour Avodeck must show. Each one is an end-to-end acceptance test (`e2e/scenarios/sNN_*.py`) run against the fixture packs and again against the launch packs. The owning slice writes the test; scenarios that span slices are owned by the later slice.

## S1. Solo expert and agency expert at once (foundation, O)
Dr K has a personal practice and is also an expert member of an agency.
- One sign-in. Today shows cases from both, each with an organisation badge, and a filter.
- Creating a case asks "Who is this case for?" (default: personal).
- Agency invoices go out under the agency's billing profile and numbering; personal ones under hers.
- Her conflict check searches all her cases in both organisations.

## S2. Expert leaves the agency mid-case (O)
Dr K is named on two agency cases (one served, one unserved) and leaves.
- Her membership ends; other agency cases disappear from her Today.
- She keeps full named-expert access to both named cases, including court-stage work.
- The agency sees metadata and administrative copies only. Both get the deletion certificate later.

## S3. Reassigning an unserved case (O)
The agency moves the unserved case to Dr M.
- Allowed only before serving; Dr K is told.
- Dr M starts with no reads and no verified facts; the facts' suggestions remain.
- Trying the same after serving returns 409 and offers "Create a replacement case" (linked, bundle copied only with recorded consent).

## S4. Single joint expert (C, F)
Instructed by both sides.
- Two instructing parties; every drafted email is addressed to both.
- Serving records two `report_servings`; deadlines run from each service date.

## S5. Wrong patient's pages in the bundle (A)
Pages 412–415 carry another person's identifiers.
- The pipeline suggests quarantine; the expert confirms.
- Pages keep their numbers and show a placeholder; facts on them are rejected; they leave search, AI, exports and citations.
- A note to the instructing parties is drafted; after sending (or 14 days) the images and text are deleted. All audited.

## S6. Clearer copy arrives (A, B)
A faint page 233 is replaced.
- The new copy is page 915; 233 is marked superseded and stays readable.
- New suggestions come from 915; the expert moves a verified fact across in one click. Nothing moves automatically.

## S7. Supplementary instructions after the questions were locked (C, E)
A second letter adds Q6.
- New instruction version; the question set reopens with a warning; existing opinions and links stay.
- The report states which instruction versions it answers.

## S8. Fact changed after it was cited (B, E, F)
The expert edits a cited result value.
- The citation shows "changed since cited"; the final check fails until the expert reviews the paragraph.

## S9. Two windows, same opinion (E)
- The second save gets 412 and the "which version do you want to keep?" prompt. Nothing is silently lost.

## S10. Court order moves a deadline, then trial is adjourned (G, I)
- The direction is recorded; the deadline moves with its history.
- The trial is adjourned to a new date; the old hearing links to the new one; the trial pack and offline package are marked stale.

## S11. Two rounds of written questions (G)
- Each round has its own deadline and answers document; answers become part of the report where the pack says so.

## S12. Court bundle re-issued (I)
- Version 2 supersedes version 1; a new concordance is built; the trial pack uses the latest confirmed version.

## S13. Appeal stops deletion (H)
Retention ends during an appeal.
- A legal hold is in place; the deletion job skips the case and tells the named expert. Releasing the hold lets deletion run the next day.

## S14. Backup restored after a deletion (H, foundation)
- The deletion ledger is replayed before the service reopens; the deleted case stays deleted.

## S15. Expert closes their account (O, H)
- Unserved personal cases are deleted after export (step-up).
- Served cases stay locked until retention; the account becomes sign-in-only for those.
- A named custodian can be given read-only custody if the expert can no longer act.

## S16. Expert withdraws (C, F)
A conflict appears after serving.
- Case status becomes withdrawn with a reason; served reports stay locked; deadlines are cancelled; a note to the instructing parties is drafted; retention starts.

## S17. Support needs to look (O)
- Support sees metadata only. The named expert grants 24-hour access to one case; every view appears in the case's audit trail; access ends automatically.
