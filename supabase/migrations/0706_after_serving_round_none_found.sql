-- 0706 after serving: a round whose document gave no questions is marked, so a later job run does not put the
-- same document to the model again. The round is still not "read" (extracted_at stays empty, the expert is
-- shown it); asking to read it again clears the mark.

set role app_migrator;

alter table app.written_question_rounds
  add column none_found_at timestamptz;

reset role;
