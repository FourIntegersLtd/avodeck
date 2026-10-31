-- Slice B: a second reading remembers how the page was turned when the expert asked (ADR 0030, rule 7).
-- The masking job refuses (`page_changed`) when the page's stored turn is not the one it was asked at, because
-- the stored picture and the words' frame may no longer agree (a half turn passes the picture-shape check).

set role app_migrator;

alter table app.second_readings add column page_rotation smallint check (page_rotation in (0, 90, 180, 270));

reset role;
