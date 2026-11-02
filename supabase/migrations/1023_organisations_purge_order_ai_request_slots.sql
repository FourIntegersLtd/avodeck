-- 1023 (foundation work): splice `ai_request_slots` (0049) into `app.case_purge_order()`, immediately
-- before `jobs` like `job_steps`: its `job_id` references `jobs` on delete cascade, so it must be
-- removed first. Written here, in the organisations range, for the same reason 1014/1019/1022 are:
-- foundation owns the purge-order contract, this file must be named for the range it sits in, and
-- placing it after every other slice's splice (and after 0112/0206/0380/0499/0599/0699/0799, which
-- each replace the function with a fixed array that would otherwise wipe a splice done at 0049)
-- means it runs last and is not itself at risk of being wiped out by a later fixed-list replacement.

set role app_migrator;

do $do$
declare
  current_order text[] := app.case_purge_order();
  mine text := 'ai_request_slots';
  at integer;
  spliced text[];
begin
  select coalesce(array_agg(t order by n), '{}') into current_order
    from unnest(current_order) with ordinality as u (t, n) where t <> mine;
  at := array_position(current_order, 'jobs');
  spliced := current_order[1:at - 1] || array[mine] || current_order[at:];
  execute format(
    'create or replace function app.case_purge_order() returns text[] language sql immutable parallel safe'
    ' set search_path = '''' as $f$ select %L::text[] $f$',
    spliced
  );
end
$do$;

reset role;
