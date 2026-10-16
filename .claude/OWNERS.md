# Owners

Each slice has one owner agent in `.claude/agents/`. An owner changes only the paths listed for it here. Paths owned by `foundation-owner` change only with approval: other owners ask foundation-owner (or the founder) rather than editing them. Contracts only grow. Keywords that route prompts to an owner are in `.claude/routes.json`.

| Paths | Owner |
|---|---|
| `backend/*`, `backend/app/*.py`, `backend/app/core/**`, `backend/app/evals/**`, `backend/app/slices/*.py`, `backend/tests/*`, `backend/tests/{core,fixtures,foundation}/**`, `worker/**`, `frontend/*`, `frontend/src/*`, `frontend/src/{design-system,api,i18n,app,lib}/**`, `frontend/src/features/foundation/**`, `frontend/tests/*`, `frontend/tests/{core,foundation}/**`, `frontend/tests/e2e/foundation/**`, `supabase/*`, `supabase/migrations/**/00??_*.sql`, `packs/**`, `evals/**`, `scripts/**`, `.claude/**`, `.github/**`, CLAUDE.md files (root and area) | foundation-owner |
| `backend/app/slices/a/**`, `frontend/src/features/a/**`, `backend/tests/a/**`, `frontend/tests/a/**`, `frontend/tests/e2e/a/**`, `supabase/migrations/**/01??_*.sql` | pipeline-owner |
| `backend/app/slices/b/**`, `frontend/src/features/b/**`, `backend/tests/b/**`, `frontend/tests/b/**`, `frontend/tests/e2e/b/**`, `supabase/migrations/**/02??_*.sql` | reader-owner |
| `backend/app/slices/c/**`, `frontend/src/features/c/**`, `backend/tests/c/**`, `frontend/tests/c/**`, `frontend/tests/e2e/c/**`, `supabase/migrations/**/03??_*.sql` | setup-owner |
| `backend/app/slices/d/**`, `frontend/src/features/d/**`, `backend/tests/d/**`, `frontend/tests/d/**`, `frontend/tests/e2e/d/**`, `supabase/migrations/**/04??_*.sql` | understand-owner |
| `backend/app/slices/e/**`, `frontend/src/features/e/**`, `backend/tests/e/**`, `frontend/tests/e/**`, `frontend/tests/e2e/e/**`, `supabase/migrations/**/05??_*.sql` | write-owner |
| `backend/app/slices/f/**`, `frontend/src/features/f/**`, `backend/tests/f/**`, `frontend/tests/f/**`, `frontend/tests/e2e/f/**`, `supabase/migrations/**/06??_*.sql` | report-owner |
| `backend/app/slices/g/**`, `frontend/src/features/g/**`, `backend/tests/g/**`, `frontend/tests/g/**`, `frontend/tests/e2e/g/**`, `supabase/migrations/**/07??_*.sql` | after-serving-owner |
| `backend/app/slices/h/**`, `frontend/src/features/h/**`, `backend/tests/h/**`, `frontend/tests/h/**`, `frontend/tests/e2e/h/**`, `supabase/migrations/**/08??_*.sql` | close-owner |
| `backend/app/slices/i/**`, `frontend/src/features/i/**`, `backend/tests/i/**`, `frontend/tests/i/**`, `frontend/tests/e2e/i/**`, `supabase/migrations/**/09??_*.sql` | trial-owner |
| `backend/app/slices/o/**`, `frontend/src/features/o/**`, `backend/tests/o/**`, `frontend/tests/o/**`, `frontend/tests/e2e/o/**`, `supabase/migrations/**/10??_*.sql` | organisations-owner |
