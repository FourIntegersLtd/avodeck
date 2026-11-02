# 0035 The transcript as Word and PDF, before reading

Date: 2026-11-02 · Status: accepted · Decided by: the founder · Builds on: rules 1, 2, 3, 6 and 10, ADR 0028 · Partly supersedes: ADR 0028 (the Word file only after reading)

**Problem.** An expert asked for the whole record organised into one file he can work through on his own, outside the app: the practice's entries by date, letters by date with the writer's role and specialty, administrative entries apart. ADR 0028 allowed only a Word copy, only of pages the named expert had already read.

**Decision.**
- **The file can be requested before any page is read.** It covers every page that is not a copy, replaced or set aside.
- **Two formats.** Word (`export_transcript`, no pictures) and PDF (`export_transcript_pdf`, a picture of every page at the back, each entry's page number linking to its picture, bookmarks for each section).
- **Arranged by the pack's sections, then by date.** `transcript.yaml` in the specialty pack lists the sections; an entry goes in the first section it matches. An entry no section claims goes in a final "Other entries" section, so nothing is left out. Administrative entries are printed in full in the file, in their own section.
- **A coverage statement** accounts for every page: with entries, with no entries, unreadable, set aside.
- **The first page says** "This file has not been checked by the expert."
- **No topic or results index.** A list of words cannot be complete, and a "None found" line would read as "nothing in the record". The files can be searched for any word. Checking for topics and results stays the expert's own reading.

**The file is a copy, never a read.** Requesting, building or downloading it records no page read and changes no read count. Rules 2 and 3 are unchanged: suggestions stay behind the 423 gate, only verified facts on read pages can be cited, and the file holds no fact value, no suggestion and no result chip.

**Still in force.** `case.export` with step-up (rule 10); the person who asks is the one who downloads; each file is handed over once and audited (`case.transcript_requested`, `case.transcript_downloaded`); the stored copy is removed after download or by the daily sweep. A case with no named expert has no file (409).

**Supersedes** these sentences of ADR 0028: "There is no Word file before reading." and "The Word file holds only pages the named expert has read, and is refused (409 `nothing_read`) when none are read."

**Consequences.** A PDF holds a picture per page (about 100 KB each after re-compressing), so a large bundle gives a large file, built in memory by the worker. Pictures are never written to disk (ADR 0021).
