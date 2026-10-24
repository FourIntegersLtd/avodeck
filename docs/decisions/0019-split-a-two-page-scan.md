# 0019 Splitting a two-page scan adds linked pages and never renumbers

Date: 2026-10-24 · Status: accepted with the build (slice A, plan "page viewer for production" item 8)

**Problem.** Some scans put two book pages on one sheet (a spread). The expert wants to read each half as its own page, with its own words and its own box around each fact. Page numbers are permanent (the instructing party and other experts see the same ones), so the split cannot replace the sheet or shift the pages after it.

**Decision.**
- The named expert (or whoever may replace a page) chooses **Split this scan**: a straight cut, down the page (two side by side) or across it (one above the other), at a position they set (default the middle).
- The original page stays in the record, unchanged: its number, picture, words, reads, facts and checks. It is shown as "Split into pages 902 and 903" and stays readable.
- The server cuts the original's one-page PDF into two one-page PDFs and puts them in a new generated record (`documents` row named for the page it was cut from, `split_of_page_id`). The existing pipeline then does what it does for a clearer copy: numbers the two pages after the case's last page (first half, then second half, in the order the expert chose), draws pictures, reads each half again (fresh word ids per half) and extracts suggestions. Nothing is renumbered.
- No new table. `documents.split_of_page_id` (unique) says a generated record is the halves of a page; when the record has been cut into pages, `pages.split_of_page_id` points each half back at the original, once. Nothing is ever removed.
- Only a page that is not set aside, not already split and not a clearer copy being replaced can be split. A half can be split again (a new row).

**Facts and reads on the original (handled safely).**
- Reads (`page_reads`, append-only) stay on the original. The halves are new pages and start unread: the blind read applies to them (423 until read).
- Checked facts (verified or edited) and anything cited by a report are never touched, moved or deleted. They still cite the original's word ids. The expert may move one to a half with the existing "move to the clearer copy" action; slice B widens the allowed targets from "the page that supersedes its own" to "the page that supersedes it, or a half of it" (a follow-up for the reader owner).
- Suggestions nobody has checked on the original are rejected as soon as the halves are numbered (the halves read the same words), the way a page read again supersedes its unchecked suggestions. The expert then sees each suggestion once, on the half it is on.
- The original is left out of duplicate matching, missing-document matching and AI input after the split, because its words are the halves' words. It stays in exports and the sorted view with its number.
- Rule 8: a served or withdrawn report version that cites the original keeps citing it; the original is never edited.

**Why rule 2 holds.** Each half has its own `ocr_words` and word ids; every new fact cites words on its half, and `provenance.check` runs as for any page. The original's words and boxes are untouched, so every old citation still points at an exact run of words on a numbered page.

**Alternatives rejected.**
- *Replace the sheet with two pages and shift the rest:* breaks permanent numbering and every shared reference.
- *Give halves letters (page 17a, 17b):* needs a second numbering scheme through the reader, report and trial slices.
- *Crop only in the viewer:* the halves would have no words or boxes of their own, so no provenance.

**Consequences.**
- Migration 0119 adds `documents.split_of_page_id` and `pages.split_of_page_id` only. The endpoint `POST /v1/cases/{id}/pages/{page_id}/split` uses the existing action `page.replace` (no change to foundation-owned permissions or purge order); the bundle summary gains `splits` (a count).
- A split adds a bundle version ("added by splitting page N").
- Cropping sets the PDF page box of each half on the original page; scans (the case this is for) are read from the cropped picture. A born-digital page's text layer is not clipped, so both halves may carry all its words; cut those by hand or not at all.
- Not in this change: automatic detection of spreads, the viewer button and the move-to-half widening (reader slice).
