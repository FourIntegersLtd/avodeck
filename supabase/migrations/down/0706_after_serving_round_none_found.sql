-- Down for 0706 after serving: drop the "no questions found" mark on a round.
set role app_migrator;

alter table app.written_question_rounds drop column if exists none_found_at;

reset role;
