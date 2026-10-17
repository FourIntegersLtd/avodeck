# Recorded model responses

`RecordedBedrock` (`app/core/ai/bedrock.py`) replays the files in this folder and refuses any request it has no recording for. Each file is `{kind, request, response}`; it is found by the sha256 of the canonical request, so the file name is only for people.

- Record with `RecordedBedrock(settings, FixtureStore(root)).record(request, response, name="setup-t06-letter")`, or `FixtureStore(root).record("bedrock", request, response, name)` (`app/core/ai/recordings.py`). Never hand-edit the `request` part: change the prompt and record again.
- Only synthetic text from `evals/generator/` goes in a recording (rule 6). The request is the text after `pii.strip`.
- Name files `<slice>-<task>-<what>.json` (slice C: `setup-*`). Slice owners own their own `<slice>-*` files here and in `../ocr/` (`.claude/OWNERS.md`).
- Changing a prompt version changes the request hash: re-record, then run `make eval`.
