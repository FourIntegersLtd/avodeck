# Pack schemas

JSON Schema for every pack file, one per file name (e.g. `jurisdiction.pack.schema.json`, `specialty.facts.schema.json`).
Written in the foundation slice. CI validates every pack (including `_fixtures/`) against them, and the loader refuses to start with an invalid pack.

Required keys per layer are listed in `docs/architecture.md` 3.7. Pack lint rules (also CI):
- jurisdiction files contain no specialty words; specialty files contain no country words (word lists in `lint_words.yaml`);
- every `{placeholder}` resolves after merging;
- every registry key has an i18n label in `web/src/foundation/i18n/`;
- `version` is bumped when any file in the pack changes.
