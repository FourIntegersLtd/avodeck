# 0006 Test-first development and vertical slices

Date: 2026-10-15 · Status: accepted

**Decision.** Red → green → refactor for every feature; tests committed failing before code; tests frozen during implementation (hook-enforced) and checked in CI. Work is split into a shared foundation plus vertical slices A–I, each owned end to end by one Claude Code session in its own worktree, with contracts agreed first.

**Why.** Lets several sessions build in parallel safely, and turns the product's safety rules into tests that can't be skipped.
