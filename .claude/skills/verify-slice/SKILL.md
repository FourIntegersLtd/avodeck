---
name: verify-slice
description: Check a finished slice before calling it done. Runs the slice tests, core tests, lint, spec check and the matching evals rows, and reports only failures. Use at the end of every slice change.
---

# Verify a slice

Slice letter `<S>` is the lower-case letter (a, b, ... i, o); for foundation use `make test-core` in place of the slice step.

1. `make test-slice S=<S>` (foundation: `make test-core`).
2. `make test-core` (the rules' tests must stay green).
3. `make lint`
4. `make check-spec`
5. Open `evals/README.md`. For each planted-item row whose "Used by" names this slice, and if the slice has produced output, compare it to the listed `expected/` file. Skip if nothing is produced yet.

Run each step with output sent to a file and read only the tail, for example `make lint > "$TMPDIR/lint.txt" 2>&1; echo "exit=$?"; tail -40 "$TMPDIR/lint.txt"`.

## Reporting
- Report only failures: the step, the test or file, and the first error line. Say "all green" otherwise.
- Never edit a test, expected file or fixture to make a check pass. If one looks wrong, stop and explain why.
- The make targets are filled in by the foundation slice. If a target does not exist yet, report "not yet available" for that step and carry on; do not count it as a failure.
