# 0008 One engine, three pack layers

Date: 2026-10-15 · Status: accepted; extended by 0011 (fourth overlay layer, inheritance, fixture packs)

**Decision.** Avodeck is a general records-review engine. GP clinical negligence breach in England and Wales is its first configuration, not its shape.
- Every case has three packs: **jurisdiction** (`gb-eaw`), **case type** (`clin-neg-breach`) and **specialty** (`general-practice`), merged by `core/packs.py`.
- Fact kinds, source types, question types, timeline lanes, checks, report sections and workflow steps are **registries the packs extend**, not fixed lists in code.
- Code says "clinician", "subject" and "instructing party"; packs supply the displayed words (GP, claimant, solicitor). "As the GP saw it" becomes "As the clinician saw it".
- Accounts are **organisations with memberships** (one-person at launch) so teams can be added without redesigning permissions.
- **Product stays narrow:** sell only GP breach until it has paying users and a stable accuracy score; then other clinical negligence specialties, then causation and condition-and-prognosis (new slice J), then personal injury, then other countries.
- A new pack must reuse at least 70% of existing screens; otherwise it is a product decision.

**Why.** Other specialties, expert roles, personal injury and adjacent sectors (law firms, NHS reviews, regulators) share most of the engine. Building the seams now costs about a week; retrofitting would touch most slices.
