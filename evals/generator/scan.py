"""Make pages look scanned or faxed, and draw handwritten Lloyd George cards.

Scanned pages lose their text layer: they are written back as image-only PDF
pages, which is what OCR has to cope with in real bundles.
"""
from __future__ import annotations

import io
import os
import random
import subprocess
import tempfile
from datetime import date

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

DJV = "/usr/share/fonts/truetype/dejavu"
LIB = "/usr/share/fonts/truetype/liberation"
DPI = 150


def pdf_pages_to_images(pdf_path: str, dpi: int = DPI) -> list[Image.Image]:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", pdf_path, os.path.join(tmp, "p")], check=True)
        files = sorted(f for f in os.listdir(tmp) if f.endswith(".png"))
        return [Image.open(os.path.join(tmp, f)).convert("RGB").copy() for f in files]


def degrade(img: Image.Image, rng: random.Random, quality: str = "scan") -> Image.Image:
    """quality: scan (light), poor (heavier), fax (1-bit, streaks)."""
    g = ImageOps.grayscale(img)
    angle = rng.uniform(-1.3, 1.3) if quality != "fax" else rng.uniform(-0.6, 0.6)
    g = g.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=255)
    # paper tone and contrast
    lo = 18 if quality == "scan" else 40
    g = g.point(lambda v: min(255, max(0, int(v * rng.uniform(0.9, 0.97) + lo))))
    if quality in ("poor", "fax"):
        g = g.filter(ImageFilter.GaussianBlur(radius=0.9 if quality == "poor" else 0.6))
    # speckle
    px = g.load()
    w, h = g.size
    n = {"scan": 900, "poor": 4000, "fax": 6000}[quality]
    for _ in range(n):
        x, y = rng.randrange(w), rng.randrange(h)
        px[x, y] = rng.choice((0, 40, 90)) if rng.random() < 0.5 else 255
    if quality == "fax":
        g = g.point(lambda v: 0 if v < 150 else 255, mode="1").convert("L")
        d = ImageDraw.Draw(g)
        for _ in range(3):
            y = rng.randrange(h)
            d.line([(0, y), (w, y)], fill=90, width=1)
    # scanner edge shadow
    d = ImageDraw.Draw(g)
    d.rectangle([0, 0, w, rng.randint(4, 14)], fill=rng.randint(150, 200))
    return g


def images_to_pdf(images: list[Image.Image], out_path: str):
    c = canvas.Canvas(out_path, pagesize=A4)
    W, H = A4
    for im in images:
        buf = io.BytesIO()
        im.convert("L").save(buf, format="JPEG", quality=72)
        buf.seek(0)
        from reportlab.lib.utils import ImageReader
        c.drawImage(ImageReader(buf), 0, 0, W, H)
        c.showPage()
    c.save()


def scan_pdf(pdf_path: str, out_path: str, seed: int, quality: str = "scan", notes=None):
    """notes: {page_index: [(text, x_frac, y_frac)]} handwritten on the paper before it is scanned."""
    rng = random.Random(seed)
    pages = pdf_pages_to_images(pdf_path)
    for i, ns in (notes or {}).items():
        annotate(pages[i], ns, seed + i)
    imgs = [degrade(im, rng, quality) for im in pages]
    images_to_pdf(imgs, out_path)


# ------------------------------------------------------------ handwriting
_HAND_FONTS = [f"{DJV}/DejaVuSans-Oblique.ttf", f"{LIB}/LiberationSerif-Italic.ttf",
               f"{DJV}/DejaVuSerif-Italic.ttf"]


def hand_text(img: Image.Image, xy, text: str, rng: random.Random, size=30, ink=(30, 35, 80), messy=0.5, hand=None,
              marks=True):
    """Handwriting, now drawn by hand.py (messy, human, per-writer profiles)."""
    from hand import write_line
    return write_line(img, xy, text, rng, size=size, ink=ink, messy=messy, hand=hand, marks=marks)


FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
LEGIBLE = ["CoveredByYourGrace.ttf", "ShadowsIntoLight.ttf", "ReenieBeanie.ttf", "JustMeAgainDownHere.ttf",
           "NothingYouCouldDo.ttf", "WaitingfortheSunrise.ttf"]
HARD = ["Zeyada.ttf", "DawningofaNewDay.ttf", "LaBelleAurore.ttf", "Cedarville-Cursive.ttf"]
HAND_SCALE = {"ReenieBeanie.ttf": 1.25, "JustMeAgainDownHere.ttf": 1.3, "Zeyada.ttf": 1.25,
              "DawningofaNewDay.ttf": 1.2, "WaitingfortheSunrise.ttf": 1.05}
INKS = [(25, 40, 120), (20, 20, 30), (30, 55, 140), (40, 30, 60)]   # blue biro, black, royal blue, faded black


def pick_hand(rng: random.Random, messy: float) -> str:
    pool = HARD if messy >= 0.8 else (LEGIBLE + HARD[:1] if messy >= 0.55 else LEGIBLE)
    return os.path.join(FONT_DIR, rng.choice(pool))


def annotate(img: Image.Image, notes, seed: int):
    """Handwritten notes on a page image: [(text, x_frac, y_frac)] e.g. a GP's 'Seen RP 28/5'."""
    rng = random.Random(seed)
    hand = pick_hand(rng, 0.6)
    ink = rng.choice(INKS)
    for text, xf, yf in notes:
        hand_text(img, (int(img.width * xf), int(img.height * yf)), text, rng, size=int(img.height / 48),
                  ink=ink, messy=0.6, hand=hand, marks=False)
    return img


def lloyd_george_card(entries: list[tuple[date, list[str]]], patient: dict, seed: int,
                      messy: float = 0.5) -> Image.Image:
    """A buff continuation card with handwritten dated entries, placed on an A4 scan."""
    rng = random.Random(seed)
    W, H = int(8.27 * DPI), int(11.69 * DPI)
    page = Image.new("RGB", (W, H), (250, 250, 248))
    cw, ch = int(W * 0.78), int(H * 0.62)
    card = Image.new("RGB", (cw, ch), rng.choice([(236, 222, 184), (240, 214, 200), (232, 220, 190)]))
    d = ImageDraw.Draw(card)
    pf = ImageFont.truetype(f"{LIB}/LiberationSans-Regular.ttf", 15)
    pfb = ImageFont.truetype(f"{LIB}/LiberationSans-Bold.ttf", 15)
    d.text((20, 14), "Surname", font=pf, fill=(90, 70, 40))
    d.text((260, 14), "Forenames", font=pf, fill=(90, 70, 40))
    d.text((cw - 230, 14), "NHS No.", font=pf, fill=(90, 70, 40))
    d.text((cw - 120, ch - 30), "FP7a", font=pfb, fill=(90, 70, 40))
    reg_hand = pick_hand(rng, 0.3)
    hand_text(card, (100, 26), patient["last"].upper(), rng, size=26, messy=0.2, hand=reg_hand, ink=INKS[1])
    hand_text(card, (360, 26), patient["first"], rng, size=26, messy=0.2, hand=reg_hand, ink=INKS[1])
    top = 60
    d.line([(0, top), (cw, top)], fill=(150, 120, 80), width=2)
    d.line([(120, top), (120, ch)], fill=(190, 80, 80), width=2)
    d.text((30, top + 6), "Date", font=pf, fill=(90, 70, 40))
    line_h = 42
    for i in range(1, (ch - top) // line_h):
        y = top + i * line_h
        d.line([(0, y), (cw, y)], fill=(170, 150, 110), width=1)
    row = 2
    for when, lines in entries:
        if row * line_h + top > ch - line_h:
            break
        hand = pick_hand(rng, messy)            # each entry by a (possibly different) doctor
        ink = rng.choice(INKS)
        y = top + row * line_h - 6
        hand_text(card, (12, y), f"{when.day}.{when.month}.{str(when.year)[2:]}", rng, size=26, messy=messy,
                  hand=hand, ink=ink, marks=False)
        for ln in lines:
            if row * line_h + top > ch - line_h:
                break
            y = top + row * line_h - 6
            hand_text(card, (132, y), ln, rng, size=27, messy=messy, hand=hand, ink=ink)
            row += 1
        if rng.random() < 0.5:                  # initials at the end of the entry
            hand_text(card, (cw - 90, top + (row - 1) * line_h - 6), rng.choice(["JC", "jc", "RW", "AMc"]), rng,
                      size=24, messy=messy, hand=hand, ink=ink)
    card = card.rotate(rng.uniform(-2, 2), expand=True, fillcolor=(250, 250, 248))
    page.paste(card, (int(W * 0.1), int(H * 0.12)))
    return page
