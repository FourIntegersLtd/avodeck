-- Undo 0031.
set role app_migrator;
drop trigger cases_named_expert_reset on app.cases;
drop function app.cases_named_expert_reset();
drop function app.word_box(uuid, text[]);
reset role;
