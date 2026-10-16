# 0009 Read embedded text first; Azure for scans; no Docling

Date: 2026-10-15 · Status: accepted

**Decision.**
- Page splitting and rendering use pikepdf/qpdf and pypdfium2 (permissive licences). PyMuPDF is not used (AGPL).
- Pages with a usable embedded text layer (born-digital letters, discharge summaries, results) take their words and boxes from the PDF itself. They skip OCR.
- Scanned, faxed and handwritten pages go to Azure Document Intelligence (Layout) in UK South, as before.
- Docling and similar local layout models are not part of the pipeline.

**Why not Docling.** Docling (IBM, MIT licence) converts documents into structure: reading order, headings and tables. It does not replace splitting, and it uses pypdfium2 itself. As an OCR replacement it is weaker where our bundles are hardest:
- on scanned and faxed pages it relies on plug-in OCR engines (EasyOCR, Tesseract, RapidOCR);
- it has little or no handwriting support;
- it gives no consistent word-level confidence, which our `word_ids` provenance and the screen 10 queue depend on;
- it needs PyTorch models, so on CPU-only App Platform workers a 3,000-page bundle could take hours.

**Why read the text layer first.** Pages that already contain text need no OCR. Reading it directly is faster, costs nothing and is exact, and provenance works the same way because the words still get IDs and boxes.

**Revisit if:** lab-result tables in born-digital PDFs extract poorly. In that case, test Docling's table model on those pages only, as an optional step.
