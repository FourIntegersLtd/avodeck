# Pack schemas

JSON Schema for every pack file: `<layer>.<file>.schema.json` (e.g. `jurisdiction.pack.schema.json`), or `<file>.schema.json`
when several layers share the file (`vocabulary`, `final_checks`, `declarations`, `facts`, `sources`, `lanes`, `checks`). For a
`.md` file the schema covers its front matter. Which files each layer may hold is listed in `api/app/core/packs.py`.
Check them with `make -C api packs`. CI validates every pack (including `_fixtures/`) against them, and the loader refuses to start with an invalid pack.

Required keys per layer are listed in `docs/architecture.md` 3.7. Pack lint rules (also CI):
- jurisdiction files contain no specialty words; specialty files contain no country words (word lists in `lint_words.yaml`);
- every `{placeholder}` resolves after merging;
- every registry key has an i18n label in `web/src/foundation/i18n/`;
- `version` is bumped when any file in the pack changes.
