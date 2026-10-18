set role app_migrator;

-- app.questions_guard() goes back to the 0019 definition: re-run it from that migration.
alter table app.questions
  drop constraint questions_outside_expertise_source,
  drop constraint questions_answer_parts_some,
  drop column outside_expertise_source,
  drop column answer_parts;

reset role;
