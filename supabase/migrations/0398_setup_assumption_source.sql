-- 0398 setup: track which letter each assumption was found in.
-- * `assumptions.source_instruction_version_id`: the instruction version this assumption was extracted
--   from, set on insert and never changed, so its page links and word ids keep pointing at the letter
--   it came from even after a newer letter re-points `instruction_version_id`.
-- The matching questions columns are in 0037; this one waits for this range because `app.assumptions`
-- is created here in 0330. `if not exists` so a database that already took the column from 0037's
-- earlier form is left as it is. RLS is the table's own (0330).

set role app_migrator;

alter table app.assumptions
  add column if not exists source_instruction_version_id uuid references app.instruction_versions (id);

update app.assumptions set source_instruction_version_id = instruction_version_id
  where source_instruction_version_id is null;

reset role;
