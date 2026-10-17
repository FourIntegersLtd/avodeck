"""The canonical page split and embedded-text reader (architecture 7 steps 1 and 2).

Every slice that needs one page of a document as its own PDF calls `core.pdf.split_pages`, so the
bytes (and so the SHA-256 that keys OCR recordings and duplicate checks) are the same everywhere.
Born-digital pages give their words from the text layer, in the same shape as OCR output.
"""

from __future__ import annotations

import hashlib
import io

import pikepdf

from app.core.ai.ocr import OcrPage, word_id
from app.core.pdf import embedded_words, split_pages

A4 = (595.0, 842.0)  # points


def _pdf(lines_per_page: list[list[str]]) -> bytes:
    pdf = pikepdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    )
    for lines in lines_per_page:
        ops = "".join(f"BT /F1 12 Tf 72 {760 - 20 * i} Td ({line}) Tj ET\n" for i, line in enumerate(lines))
        page = pikepdf.Page(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Page,
                MediaBox=[0, 0, *A4],
                Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
                Contents=pdf.make_stream(ops.encode()),
            )
        )
        pdf.pages.append(page)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


SOURCE = _pdf([["Synthetic page one"], ["Synthetic page two", "second line here"], []])


def test_split_gives_one_single_page_pdf_per_page_in_order() -> None:
    pages = split_pages(SOURCE)
    assert len(pages) == 3
    for one in pages:
        with pikepdf.open(io.BytesIO(one)) as pdf:
            assert len(pdf.pages) == 1
    assert embedded_words(pages[0]).words[0].text == "Synthetic"
    assert [w.text for w in embedded_words(pages[1]).words][-1] == "here"


def test_split_is_deterministic_so_page_hashes_are_stable() -> None:
    first = [hashlib.sha256(p).hexdigest() for p in split_pages(SOURCE)]
    again = [hashlib.sha256(p).hexdigest() for p in split_pages(SOURCE)]
    assert first == again
    assert len(set(first)) == 3, "different pages give different bytes"
    # Splitting a page that is already on its own gives the same bytes again.
    assert split_pages(split_pages(SOURCE)[1]) == [split_pages(SOURCE)[1]]


def test_embedded_words_come_in_reading_order_with_ids_boxes_and_full_confidence() -> None:
    page = embedded_words(split_pages(SOURCE)[1])
    assert isinstance(page, OcrPage)
    assert page.number == 1
    assert (round(page.width, 2), round(page.height, 2)) == (round(A4[0] / 72, 2), round(A4[1] / 72, 2))
    assert [w.text for w in page.words] == ["Synthetic", "page", "two", "second", "line", "here"]
    assert [w.id for w in page.words] == [word_id(1, n) for n in range(1, 7)] == [f"p1w{n}" for n in range(1, 7)]
    for w in page.words:
        assert w.confidence == 1.0 and not w.handwritten and not w.struck
        assert len(w.polygon) == 8
        xs, ys = w.polygon[0::2], w.polygon[1::2]
        assert 0 <= min(xs) < max(xs) <= page.width
        assert 0 <= min(ys) < max(ys) <= page.height
    first, fourth = page.words[0], page.words[3]
    assert first.polygon[1] < fourth.polygon[1], "top-left origin: the second line is lower down"
    assert "Synthetic" not in repr(page), "page text never appears in a repr"


def test_a_page_with_no_text_layer_has_no_words() -> None:
    assert embedded_words(split_pages(SOURCE)[2]).words == ()
