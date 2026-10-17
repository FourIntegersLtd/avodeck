"""Where every word drawn on a page image ends up, so OCR recordings can be written from the truth.

`install()` wraps Pillow: typed text drawn with `ImageDraw.text`, and handwriting written by
`hand.write_line` (which calls `add`), are noted on the image as word boxes. The boxes follow the
image through `paste`, `rotate`, `resize`, `copy` and `convert`, so a card pasted onto a page or a
page turned sideways keeps its words in the right place. Recording never draws from a random
stream, so the bundle is the same with or without it.

A word is `{"text", "poly": [x1, y1, ... x4, y4] (pixels, top-left origin), "handwritten", "struck"}`.
The scanner's small tilt (about a degree) is not followed; the boxes are a stand-in, not a measurement.
"""
from __future__ import annotations

import math
from contextlib import contextmanager

from PIL import Image, ImageDraw

ATTR = "_avo_words"
_suppress = [0]
_installed = [False]


def words(img: Image.Image) -> list[dict]:
    return getattr(img, ATTR, None) or []


def _put(img: Image.Image, items: list[dict]) -> None:
    if items:
        setattr(img, ATTR, words(img) + items)


def add(img: Image.Image, text: str, box, *, handwritten: bool, struck: bool = False) -> None:
    if _suppress[0] or not text.strip():
        return
    x0, y0, x1, y1 = (float(v) for v in box)
    if not (0 <= (x0 + x1) / 2 < img.width and 0 <= (y0 + y1) / 2 < img.height):
        return                                 # drawn off the edge of the paper: nothing to read
    x0, y0, x1, y1 = max(x0, 0.0), max(y0, 0.0), min(x1, float(img.width)), min(y1, float(img.height))
    _put(img, [{"text": text, "poly": [x0, y0, x1, y0, x1, y1, x0, y1], "handwritten": handwritten,
                "struck": struck}])


def mark_struck(img: Image.Image, since: int) -> None:
    for w in words(img)[since:]:
        w["struck"] = True


def mark_struck_between(img: Image.Image, x0: float, x1: float, y: float) -> None:
    """A scribble across a line: words it covers are struck."""
    for w in words(img):
        xs, ys = w["poly"][0::2], w["poly"][1::2]
        if min(xs) < x1 and max(xs) > x0 and min(ys) - 4 <= y <= max(ys) + 4:
            w["struck"] = True


@contextmanager
def suppressed():
    _suppress[0] += 1
    try:
        yield
    finally:
        _suppress[0] -= 1


def _moved(items: list[dict], fn) -> list[dict]:
    out = []
    for w in items:
        pts = [fn(w["poly"][i], w["poly"][i + 1]) for i in range(0, 8, 2)]
        out.append({**w, "poly": [c for p in pts for c in p]})
    return out


def install() -> None:
    if _installed[0]:
        return
    _installed[0] = True
    text0, paste0, rotate0, resize0 = ImageDraw.ImageDraw.text, Image.Image.paste, Image.Image.rotate, Image.Image.resize
    copy0, convert0 = Image.Image.copy, Image.Image.convert

    def text(self, xy, text, fill=None, font=None, anchor=None, spacing=4, *args, **kwargs):
        text0(self, xy, text, fill, font, anchor, spacing, *args, **kwargs)
        img = getattr(self, "_image", None)
        if img is None or _suppress[0] or not isinstance(text, str) or font is None or "\n" in text:
            return
        try:
            left, top, right, bottom = self.textbbox(xy, text, font=font, anchor=anchor)
        except (TypeError, ValueError):
            return
        pos = 0
        for word in text.split():
            start = text.index(word, pos)
            pos = start + len(word)
            a = left + font.getlength(text[:start])
            b = left + font.getlength(text[:pos])
            add(img, word, (a, top, max(b, a + 1), bottom), handwritten=False)

    def paste(self, im, box=None, mask=None):
        paste0(self, im, box, mask)
        if isinstance(im, Image.Image) and words(im) and not _suppress[0]:
            dx, dy = (box[0], box[1]) if isinstance(box, tuple | list) else (0, 0)
            _put(self, _moved(words(im), lambda x, y: (x + dx, y + dy)))

    def rotate(self, angle, resample=Image.Resampling.NEAREST, expand=False, center=None, translate=None,
               fillcolor=None):
        out = rotate0(self, angle, resample, expand, center, translate, fillcolor)
        if words(self) and center is None and translate is None:
            w, h = self.size
            nw, nh = out.size
            t = math.radians(angle)
            c, s = math.cos(t), math.sin(t)

            def turn(x, y):
                dx, dy = x - w / 2, y - h / 2
                return (dx * c + dy * s + nw / 2, -dx * s + dy * c + nh / 2)

            setattr(out, ATTR, _moved(words(self), turn))
        return out

    def resize(self, size, *args, **kwargs):
        out = resize0(self, size, *args, **kwargs)
        if words(self):
            fx, fy = out.size[0] / self.size[0], out.size[1] / self.size[1]
            setattr(out, ATTR, _moved(words(self), lambda x, y: (x * fx, y * fy)))
        return out

    def copy(self):
        out = copy0(self)
        if words(self):
            setattr(out, ATTR, [dict(w) for w in words(self)])
        return out

    def convert(self, *args, **kwargs):
        out = convert0(self, *args, **kwargs)
        if words(self) and out is not self:
            setattr(out, ATTR, [dict(w) for w in words(self)])
        return out

    ImageDraw.ImageDraw.text = text
    Image.Image.paste = paste
    Image.Image.rotate = rotate
    Image.Image.resize = resize
    Image.Image.copy = copy
    Image.Image.convert = convert


def in_inches(img: Image.Image, page_w_in: float, page_h_in: float) -> list[dict]:
    """The image's words with boxes in inches on a page the image fills (as `images_to_pdf` draws it)."""
    fx, fy = page_w_in / img.width, page_h_in / img.height
    return [{**w, "poly": [round(v * (fx if i % 2 == 0 else fy), 4) for i, v in enumerate(w["poly"])]}
            for w in words(img)]
