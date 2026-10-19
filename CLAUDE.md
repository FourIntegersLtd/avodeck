@AGENTS.md

## Working cheaply (applies in cloud sessions too)

Every tool call replays the whole context, so the number of calls is the main cost. Aim for the fewest calls that still give a verified result.

- Build a well-specified task on Sonnet; use Opus only for design, review against a baseline, debugging an unknown cause, or anything that moves money or touches auth. Set `model` on every Agent call; never use `general-purpose` for read-only work.
- Read-only sweeps needing more than 2–3 greps: the `Explore` agent on haiku. Test/lint suites and fully specified edits: the `worker` agent (haiku); it returns only failures. At most 3–4 subagents at once.
- Issue every read you need in one turn as parallel calls; locate (grep/glob) before reading, then read only the line ranges you need. Never re-read a file after your own edit. Batch related greps into one shell call.
- Long output goes to a file; read the tail: `cmd > "$TMPDIR/out.txt" 2>&1; echo "exit=$?"; tail -40 "$TMPDIR/out.txt"`. Check exit codes, not logs.
- If the task is clear, act. Ask only when the answer changes what you do. Result first in replies, 10 lines or fewer, with `path:line`, how it was verified, what is left.
- If one step is blocked (a refused command, a merge you cannot do), note it and carry on with every later step that does not depend on it; never stop the whole plan.

## Environment facts

- Python deps via Poetry on 3.12, never uv. Apply migrations with `supabase migration up --db-url "postgresql://postgres:postgres@127.0.0.1:<port>/postgres?sslmode=disable" --include-all`, never `--local`. Tests read `TEST_DATABASE_ADMIN_URL`, `TEST_DATABASE_URL` (`app_api:app_api_local`) and `TEST_DATABASE_URL_DIRECT` (`app_worker:app_worker_local`).
- Never commit `supabase/config.toml`; never force push; never read `.env` files or secrets.
- On the founder's Mac, DB ports 55322 and 56322 are the app and founder databases: never run tests against them.
