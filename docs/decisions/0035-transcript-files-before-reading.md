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

**Received dates.** A letter's heading may carry the date the page says it was received, taken from the page's availability evidence. That value is cited to the page's words (rule 2), is printed as it was read, and is not a verified fact: the expert still reads the page and checks it before relying on it. The file's first-page line says the file has not been checked.

**Known limits.** The PDF embeds open-licence fonts (Atkinson Hyperlegible, IBM Plex Mono, PT Serif), so characters those fonts lack (for example CJK) print as blanks in the record text; Word is unaffected. A bundle of 4,000 pages makes a large PDF (pictures are shrunk as they are fetched).

**Layout of the files.** The PDF, the Word copy and the screen share one structure, like a dissertation: a cover; "About this transcript" (what it is, the coverage table, what each section holds); contents; numbered sections, each split by year and month with the oldest entry first and a page reference on every entry; then appendices (pages with no entries, pages the computer couldn't read, and, in the PDF only, pictures of the pages). In the PDF the contents rows and page references are clickable, sections are bookmarked, and every page after the cover carries a running header and "Page x of y". The Word copy has linked contents and a page-number footer. The screen opens on the contents and shows one section at a time (`?section=<key>`); the opening view shows no entry text and records no read. The first page of each file still says it has not been checked by the expert, and the structure adds no fact, suggestion or AI prose.
