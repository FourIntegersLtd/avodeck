-- 0395 setup (slice C): a short title for each question (screens 06 and 14's tabs).
-- A label of at most 40 characters, set by the `extract_questions` job from the model's `short_title` or the
-- first clause of the question's own words. Null on questions read before this migration; the API then
-- answers with the first clause. The exact words (`text_verbatim`) are untouched.

set role app_migrator;

alter table app.questions
  add column short_title text check (char_length(short_title) between 1 and 40);

reset role;
