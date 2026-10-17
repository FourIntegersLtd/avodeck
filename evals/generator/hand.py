"""Messy, human handwriting for synthetic records.

Modelled on what real clinicians' notes look like:
- fast right-slanted cursive where only the first letters of a word are clear and the rest
  collapses into a wavy line with a trailing tail (doctors' "scrawl");
- lines that climb or sag and crowd into the margin; sizes that jump around;
- words written over ruled lines, uneven pressure, biro ink that skips;
- crossings-out with the word rewritten, underlining, circled numbers, arrows for up and down;
- writers get sloppier further along a line and further down a page (fatigue).

Each writer has a fixed profile (font, slant, size, pressure, messiness) so one person's entries
look alike; every word is still distorted differently (elastic deformation), so no two letters match.
"""
from __future__ import annotations

import hashlib
import os
import random
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from scipy.ndimage import gaussian_filter, map_coordinates

import layout

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")

SCRAWL = ["Kristi-Regular.ttf", "HerrVonMuellerhoff-Regular.ttf", "MrsSaintDelafield-Regular.ttf", "Meddon.ttf",
          "MrDafoe-Regular.ttf", "Zeyada.ttf", "DawningofaNewDay.ttf", "LaBelleAurore.ttf", "Cedarville-Cursive.ttf",
          "HomemadeApple-Regular.ttf"]
EVERYDAY = ["SueEllenFrancisco-Regular.ttf", "OvertheRainbow.ttf", "NanumPenScript-Regular.ttf", "ReenieBeanie.ttf",
            "NothingYouCouldDo.ttf", "WaitingfortheSunrise.ttf", "ShadowsIntoLight.ttf", "CoveredByYourGrace.ttf",
            "LovedbytheKing.ttf", "TheGirlNextDoor.ttf", "GiveYouGlory.ttf", "JustMeAgainDownHere.ttf",
            "IndieFlower-Regular.ttf", "GochiHand-Regular.ttf", "SwankyandMooMoo.ttf", "AnnieUseYourTelescope-Regular.ttf",
            "WalterTurncoat-Regular.ttf", "Schoolbell-Regular.ttf", "RockSalt-Regular.ttf"]
CURSIVE = set(SCRAWL) | {"WaitingfortheSunrise.ttf", "NothingYouCouldDo.ttf"}
# relative x-height correction so every font lands at a similar size
SCALE = {"Kristi-Regular.ttf": 1.5, "HerrVonMuellerhoff-Regular.ttf": 1.6, "MrsSaintDelafield-Regular.ttf": 1.55,
         "Meddon.ttf": 0.9, "MrDafoe-Regular.ttf": 1.35, "Zeyada.ttf": 1.35, "DawningofaNewDay.ttf": 1.3,
         "LaBelleAurore.ttf": 1.0, "Cedarville-Cursive.ttf": 1.0, "HomemadeApple-Regular.ttf": 0.85,
         "ReenieBeanie.ttf": 1.3, "JustMeAgainDownHere.ttf": 1.35, "SueEllenFrancisco-Regular.ttf": 1.3,
         "NanumPenScript-Regular.ttf": 1.3, "RockSalt-Regular.ttf": 0.8, "SwankyandMooMoo.ttf": 1.0}
SYMBOLS = [(r"\bincreas\w*\b", "↑"), (r"\bdecreas\w*\b", "↓"), (r"\bdiagnosis\b", "Δ"), (r"\bfracture\b", "#"),
           (r"\bwith\b", "c̄"), (r"\bno abnormality\b", "NAD")]
INKS = [(25, 40, 120), (20, 20, 30), (30, 55, 140), (40, 30, 60), (10, 25, 90)]


class Writer:
    """A person's handwriting. messy: 0 neat ... 1 a scrawl."""

    def __init__(self, seed_key: str, messy: float, hand: str | None = None, ink=None):
        h = int(hashlib.sha1(f"{seed_key}|{messy}".encode()).hexdigest()[:12], 16)
        r = random.Random(h)
        self.messy = messy
        if hand and os.path.basename(hand) in SCRAWL + EVERYDAY:
            self.font = os.path.basename(hand)
        else:
            self.font = r.choice(SCRAWL if messy >= 0.6 else EVERYDAY)
        if messy >= 0.6 and self.font not in SCRAWL:
            self.font = r.choice(SCRAWL)
        self.path = os.path.join(FONT_DIR, self.font)
        self.cursive = self.font in CURSIVE
        self.slant = r.uniform(4, 22) if messy >= 0.6 else r.uniform(-8, 14)   # degrees of shear to the right
        self.size = r.uniform(0.85, 1.25)
        self.spacing = r.uniform(0.18, 0.5)
        self.climb = r.uniform(-0.025, 0.035) * (0.4 + messy)                # baseline slope
        self.pressure = r.choice([-1, 0, 0, 0, 1])                        # stroke thickness
        self.collapse = messy >= 0.6 and r.random() < 0.85                  # word endings flatten into waves
        self.ink = ink or r.choice(INKS)
        self.skips = r.random() < 0.5                                         # biro skipping
        self.caps = r.random() < 0.15 and not self.cursive                   # writes in capitals


_WRITERS: dict = {}


def writer_for(hand, messy, ink):
    key = (hand, round(messy, 2), ink)
    if key not in _WRITERS:
        _WRITERS[key] = Writer(str(key), messy, hand, ink)
    return _WRITERS[key]


def _elastic(a: np.ndarray, rng: np.random.Generator, alpha: float, sigma: float) -> np.ndarray:
    if alpha <= 0:
        return a
    dx = gaussian_filter(rng.uniform(-1, 1, a.shape), sigma) * alpha
    dy = gaussian_filter(rng.uniform(-1, 1, a.shape), sigma) * alpha
    y, x = np.meshgrid(np.arange(a.shape[0]), np.arange(a.shape[1]), indexing="ij")
    return map_coordinates(a, [y + dy, x + dx], order=1, mode="constant")


def _render_word(word: str, w: Writer, s: int, rng: random.Random, fatigue: float) -> Image.Image:
    """Grey mask (L) of one word, distorted. Its own drawing is not page text (layout capture is off)."""
    with layout.suppressed():
        return _render_word_mask(word, w, s, rng, fatigue)


def _render_word_mask(word: str, w: Writer, s: int, rng: random.Random, fatigue: float) -> Image.Image:
    f = ImageFont.truetype(w.path, max(8, s))
    mess = min(1.0, w.messy + fatigue)
    if w.cursive or len(word) < 2:
        bb = f.getbbox(word)
        img = Image.new("L", (bb[2] - bb[0] + s, int(s * 2.2)), 0)
        ImageDraw.Draw(img).text((s // 2 - bb[0], int(s * 0.4)), word, font=f, fill=255)
    else:
        # print writers: letters placed one by one with bounce, size and rotation changes
        parts, x = [], s // 3
        for ch in word:
            fs = max(8, int(s * rng.uniform(0.92, 1.08)))
            ff = ImageFont.truetype(w.path, fs)
            bb = ff.getbbox(ch) if ch.strip() else (0, 0, fs // 3, fs)
            g = Image.new("L", (bb[2] - bb[0] + 10, int(fs * 2)), 0)
            ImageDraw.Draw(g).text((5 - bb[0], int(fs * 0.35)), ch, font=ff, fill=255)
            g = g.rotate(rng.uniform(-5, 5) * (0.4 + mess), resample=Image.BICUBIC, expand=True)
            parts.append((g, x, int(rng.uniform(-0.05, 0.05) * s * (0.5 + mess))))
            x += (bb[2] - bb[0]) * rng.uniform(0.82, 1.08) + rng.uniform(0, 0.08) * s
        img = Image.new("L", (int(x + s), int(s * 2.4)), 0)
        for g, gx, gy in parts:
            img.paste(g, (int(gx), int(s * 0.2 + gy)), g)
    a = np.asarray(img, dtype=np.float32) / 255.0
    # scrawl: keep the first letters, flatten and squash the rest into a wavy line with a tail
    if w.collapse and len(word) > 4 and rng.random() < 0.3 + 0.35 * mess:
        keep = int(a.shape[1] * rng.uniform(0.3, 0.5))
        tail = a[:, keep:]
        if tail.shape[1] > 4:
            th = tail.shape[0]
            squash_x = rng.uniform(0.45, 0.75)
            squash_y = rng.uniform(0.35, 0.6)
            t = Image.fromarray((tail * 255).astype(np.uint8)).resize(
                (max(2, int(tail.shape[1] * squash_x)), max(2, int(th * squash_y))))
            canvas = Image.new("L", (t.width + int(s * 0.6), th), 0)
            mid = int(th * 0.55) - t.height // 2
            canvas.paste(t, (0, mid))
            if rng.random() < 0.35:       # occasional trailing flick, curved
                d = ImageDraw.Draw(canvas)
                yb = mid + int(t.height * 0.7)
                L = s * rng.uniform(0.2, 0.45)
                pts = [(t.width - 2 + k * L / 6, yb - np.sin(k / 6 * 2.2) * s * 0.12) for k in range(7)]
                d.line(pts, fill=210, width=max(1, s // 16))
            a = np.concatenate([a[:, :keep], np.asarray(canvas, dtype=np.float32) / 255.0], axis=1)
    # slant (shear)
    shear = np.tan(np.radians(w.slant + rng.uniform(-4, 4) * (0.5 + mess)))
    im = Image.fromarray((a * 255).astype(np.uint8))
    pad = int(abs(shear) * im.height) + 2
    im = im.transform((im.width + pad, im.height), Image.AFFINE, (1, shear, -pad if shear > 0 else 0, 0, 1, 0),
                      resample=Image.BICUBIC)
    a = np.asarray(im, dtype=np.float32) / 255.0
    nrng = np.random.default_rng(rng.randrange(1 << 30))
    a = _elastic(a, nrng, alpha=s * (0.2 + 0.7 * mess), sigma=s * 0.25)      # big wobble
    a = _elastic(a, nrng, alpha=s * 0.05 * (0.5 + mess), sigma=max(1.0, s * 0.04))   # tremor
    im = Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8))
    if w.pressure > 0:
        im = im.filter(ImageFilter.MaxFilter(3))
    a = np.asarray(im, dtype=np.float32) / 255.0
    # uneven ink and biro skips
    a *= np.clip(gaussian_filter(nrng.uniform(0.65, 1.0, a.shape), s * 0.15) * 1.35, 0.72, 1.0)
    if w.skips:
        for _ in range(rng.randint(0, 1)):
            x0 = int(rng.random() * a.shape[1])     # one draw whatever the width (fonts measure differently)
            a[:, x0:x0 + rng.randint(1, max(2, s // 14))] *= rng.uniform(0.2, 0.6)
    return Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8))


def _abbreviate(text: str, w: Writer, rng: random.Random) -> str:
    if w.messy < 0.5:
        return text
    for pat, sym in SYMBOLS:
        if rng.random() < 0.6:
            text = re.sub(pat, sym, text, flags=re.I)
    return text


def _ink_box(x: int, y: int, mask: Image.Image):
    """Where a word's ink lies once its mask is pasted at (x, y) (the mask has blank padding)."""
    bb = mask.getbbox() or (0, 0, mask.width, mask.height)
    return (x + bb[0], y + bb[1], x + bb[2], y + bb[3])


def write_line(img: Image.Image, xy, text: str, rng: random.Random, size=30, ink=None, messy=0.5, hand=None,
               max_x: int | None = None, marks: bool = True) -> int:
    """Write one line of handwriting onto img starting at xy (x, baseline-ish y). Returns end x."""
    w = writer_for(hand, messy, ink)
    ink = ink or w.ink
    x, y = xy
    x0 = x
    max_x = max_x or img.width - 30
    s_base = size * w.size * SCALE.get(w.font, 1.1)
    line_wave = rng.uniform(0, 6.28)
    text = _abbreviate(text.upper() if w.caps else text, w, rng)
    draw = ImageDraw.Draw(img)
    crossed_once = False
    short = len(text.split()) <= 2          # chart cells, dates, initials: corrections are rare there
    if short and rng.random() > 0.08:
        marks = False
    for i, word in enumerate(text.split()):
        frac = (x - x0) / max(1, (max_x - x0))
        fatigue = 0.25 * frac * w.messy
        s = int(s_base * rng.uniform(0.85, 1.15) * (1 - 0.15 * frac if w.messy > 0.6 else 1))
        mask = _render_word(word, w, s, rng, fatigue)
        if x + mask.width > max_x + s * 1.5:          # crowd into the margin rather than wrap neatly
            mask = mask.resize((max(4, int(mask.width * 0.7)), mask.height))
        drift = max(-s * 0.45, min(s * 0.45, (x - x0) * w.climb))
        yy = y + drift + np.sin(line_wave + (x - x0) / (s * 6)) * s * 0.12 * (0.5 + w.messy) \
            + rng.uniform(-0.08, 0.08) * s - mask.height * 0.62
        colour = Image.new("RGB", mask.size, ink)
        img.paste(colour, (int(x), int(yy)), mask)
        n_before = len(layout.words(img))
        layout.add(img, word, _ink_box(int(x), int(yy), mask), handwritten=True)
        # crossing out a false start, then the word again
        if marks and not crossed_once and len(word) > 2 and rng.random() < 0.02 + 0.03 * w.messy:
            crossed_once = True
            for k in range(rng.randint(1, 2)):
                draw.line([(x + 2, yy + mask.height * rng.uniform(0.45, 0.65)),
                           (x + mask.width - 4, yy + mask.height * rng.uniform(0.4, 0.6))], fill=ink, width=max(2, s // 12))
            layout.mark_struck(img, n_before)
            x += mask.width + s * 0.2
            mask = _render_word(word, w, s, rng, fatigue)
            ry = int(yy + rng.uniform(-3, 3))
            img.paste(Image.new("RGB", mask.size, ink), (int(x), ry), mask)
            layout.add(img, word, _ink_box(int(x), ry, mask), handwritten=True)
        # circle a number, underline an important word
        if marks and any(ch.isdigit() for ch in word) and rng.random() < 0.05 + 0.05 * w.messy:
            draw.ellipse([x - 4, yy + mask.height * 0.15, x + mask.width + 4, yy + mask.height * 0.95], outline=ink,
                         width=max(1, s // 16))
        elif marks and rng.random() < 0.03:
            draw.line([(x, yy + mask.height * 0.85), (x + mask.width, yy + mask.height * 0.82)], fill=ink, width=max(1, s // 14))
        x += mask.width * rng.uniform(0.82, 0.98) + s * w.spacing * rng.uniform(0.5, 1.4)
    return int(x)


def signature(img: Image.Image, xy, name: str, rng: random.Random, size=30, ink=None, messy=0.8, hand=None):
    """A squiggle signature, sometimes with the name printed after it."""
    w = writer_for(hand, max(messy, 0.7), ink)
    x, y = xy
    ink = ink or w.ink
    d = ImageDraw.Draw(img)
    pts = []
    for k in range(rng.randint(14, 26)):
        pts.append((x + k * size * 0.16, y - size * 0.2 + np.sin(k * rng.uniform(0.9, 1.6)) * size * rng.uniform(0.15, 0.45)))
    d.line(pts, fill=ink, width=max(2, size // 12))
    d.line([(x - 4, y + size * 0.15), (pts[-1][0] + 8, y + size * 0.05)], fill=ink, width=max(1, size // 16))
    if rng.random() < 0.6:
        write_line(img, (int(pts[-1][0] + size * 0.6), y), name, rng, size=int(size * 0.75), ink=ink, messy=0.35, marks=False)


# ------------------------------------------------------------ ward-round corrections
WRONG_PLANS = ["IV co-amoxiclav 1.2g tds", "NBM from midnight", "CT abdo today", "Stop enoxaparin", "Bloods 4-hourly",
               "Refer surgeons", "Increase oxycodone to 10mg", "Repeat ECG", "Discharge today", "Fluids 1L 4-hourly"]


def _strike(img, x0, x1, y, ink, width, rng):
    d = ImageDraw.Draw(img)
    d.line([(x0, y + rng.uniform(-3, 3)), (x1, y + rng.uniform(-3, 3))], fill=ink, width=width)


def _scribble(img, x0, x1, y, h, ink, rng):
    # The zig-zag's own random stream: how many points it has depends on measured widths, which differ
    # between font renderers, so it must not use up draws from the shared stream.
    rng = random.Random(rng.random())
    d = ImageDraw.Draw(img)
    pts = []
    x = x0
    up = True
    while x < x1:
        pts.append((x, y - h * (0.5 if up else -0.1) + rng.uniform(-4, 4)))
        x += rng.uniform(5, 12)
        up = not up
    if len(pts) > 1:
        d.line(pts, fill=ink, width=3)
        d.line([(p[0] + 3, p[1] + rng.uniform(-6, 6)) for p in pts], fill=ink, width=2)
    layout.mark_struck_between(img, x0, x1, y - h * 0.3)


def ward_entry(img, x_text, y, lines, rng, line_h, size=27, ink=None, messy=0.6, hand=None, initials="JC",
               time_str="", max_x=None):
    """Write an entry's lines with the corrections real notes have. Returns (new_y, struck_texts)."""
    w = writer_for(hand, messy, ink)
    ink = ink or w.ink
    struck = []
    max_x = max_x or img.width - 60
    out_lines = list(lines)
    # a wrong plan line, crossed through with a single line and initialled, then the right line
    if rng.random() < 0.45:
        pos = rng.randint(1, len(out_lines)) if len(out_lines) > 1 else 1
        out_lines.insert(pos, ("STRIKE", rng.choice(WRONG_PLANS)))
    # an inserted word with an arrow from the margin
    insert_at = rng.randrange(len(lines)) if rng.random() < 0.25 else None
    for i, ln in enumerate(out_lines):
        if isinstance(ln, tuple):
            txt = ln[1]
            since = len(layout.words(img))
            end = write_line(img, (x_text, y), txt, rng, size=size, ink=ink, messy=messy, hand=hand, max_x=max_x)
            _strike(img, x_text - 4, end, y - size * 0.28, ink, max(2, size // 13), rng)
            layout.mark_struck(img, since)
            write_line(img, (end + 10, y), f"error {initials}", rng, size=int(size * 0.7), ink=ink, messy=0.4, hand=hand, marks=False)
            struck.append(txt)
            y += line_h
            continue
        end = write_line(img, (x_text, y), ln, rng, size=size, ink=ink, messy=messy, hand=hand, max_x=max_x)
        if rng.random() < 0.06:          # one word scribbled out mid-line, then carry on
            sx = x_text + rng.uniform(0.3, 0.7) * max(10, end - x_text)
            _scribble(img, sx, sx + size * rng.uniform(1.5, 3), y, size, ink, rng)
            struck.append("(scribbled word)")
        if i == insert_at:
            d = ImageDraw.Draw(img)
            ax = min(end + 20, max_x - 120)
            d.line([(ax, y - size * 0.2), (ax - 25, y - size * 0.9)], fill=ink, width=2)      # caret/arrow
            write_line(img, (int(ax - 20), int(y - size * 1.05)), rng.choice(["?sepsis", "↑ pain", "rpt", "NB", "d/w reg"]),
                       rng, size=int(size * 0.7), ink=ink, messy=messy, hand=hand)
        if ln.lower().startswith(("plan", "imp")) and rng.random() < 0.5:
            d = ImageDraw.Draw(img)
            d.line([(x_text, y + 4), (x_text + size * 1.6, y + 3)], fill=ink, width=2)        # underlined heading
        y += line_h
    return y, struck


def crossed_section(img, x0, y0, x1, y1, rng, ink, initials="JC", note="written in error - wrong patient"):
    """A whole block crossed through with a big X and a note, as when notes go in the wrong patient's file."""
    d = ImageDraw.Draw(img)
    d.line([(x0, y0), (x1, y1)], fill=ink, width=3)
    d.line([(x0, y1), (x1, y0)], fill=ink, width=3)
    write_line(img, (int(x0 + (x1 - x0) * 0.25), int((y0 + y1) / 2)), f"{note} {initials}", rng, size=26, ink=ink, messy=0.4)
