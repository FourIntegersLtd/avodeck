# 0030 Vision with masking (T-10 second reading)

Date: 2026-10-31 · Status: accepted · Decided by: the founder (plan "lazy-mapping-waterfall", item 6) · Builds on: rules 1, 2, 6 and 7, ADR 0028

**Problem.** Some pages are handwriting the computer cannot read. The expert types them out, and wants a second pair of eyes that copies the writing. Only the picture will do, and a picture can show a name or number that no text step can strip.

**Decision.**
- **The picture is masked before it can leave.** `core/ai/mask.py` paints a black box (the word's polygon plus a margin) over every OCR word that touches an identifier span (`pii.spans`: the same choice as `pii.strip`, as character ranges). Only a `MaskedImage`, built by `mask_page`, can be given to `AiKit.run_image`; raw bytes are refused.
- **Fail closed.** `MaskUnsafe` is raised, and the request is refused with a reason code, when there are no words, the page reads too poorly (mean confidence under 0.35), the picture is not the size the OCR frame implies, an identifier span has no word, nothing at all was masked, or a touched word has no place on the page. Nothing is sent and the reason (a code, never text) is stored.
- **Expert-typed pages are read again for boxes.** The typed words have no place on the picture, so the job runs OCR on the page again and masks from those boxes; if that is not possible the request is refused (`no_place`).
- **PNG, not JPEG.** The masked picture is lossless, so black stays exactly black and the guard test can check pixels. Its SHA-256 is recorded with the request; the bytes never are (rule 6).
- **Never opinion, never a fact.** T-10 copies the writing, line by line, with a confidence. Its lines are stored sealed in `second_readings`, shown beside the page, and never written into the expert's text or turned into facts (rules 1 and 2). Only the named expert and their assistant may ask (`page.transcribe`).
- **In region.** The picture goes only to Bedrock London, in-region model IDs (rule 5).

**Rejected.** Sending the picture and trusting the model to ignore names (breaks rule 7). Blurring (can be reversed). Masking by the word's box alone with no confidence floor (the box of a badly read word is a guess).
