"""Which language a page is in (F37, architecture 10.3 "Foreign-language pages").

An offline, seeded detector reads the page's words in memory (never stored or logged). A page is in
the pack's language when that language is plausible for it; another language is named only when it
is clearly that one; a page with too few words tells nothing. Nothing is ever translated.
"""

from __future__ import annotations

import re
import threading
from functools import cache

from langdetect import DetectorFactory  # type: ignore[import-untyped]
from langdetect.lang_detect_exception import LangDetectException  # type: ignore[import-untyped]

MIN_WORDS = 20  # words of two or more letters
PLAUSIBLE = 0.2  # the pack's language needs at least this share
CLEAR = 0.8  # another language needs at least this share
SEED = 0

_WORD = re.compile(r"[^\W\d_]{2,}")
_lock = threading.Lock()


@cache
def _factory() -> DetectorFactory:
    factory = DetectorFactory()
    factory.seed = SEED
    factory.load_profile(_profiles())
    return factory


def _profiles() -> str:
    from langdetect import detector_factory

    return str(detector_factory.PROFILES_DIRECTORY)


def _base(code: str) -> str:
    return code.split("-")[0].lower()


def detect(text: str, expected: str) -> str | None:
    words = _WORD.findall(text)
    if len(words) < MIN_WORDS:
        return None
    with _lock:
        detector = _factory().create()
        detector.append(" ".join(words))
        try:
            found = detector.get_probabilities()
        except LangDetectException:
            return None
    shares: dict[str, float] = {}
    for item in found:
        shares[_base(item.lang)] = shares.get(_base(item.lang), 0.0) + float(item.prob)
    wanted = _base(expected)
    if shares.get(wanted, 0.0) >= PLAUSIBLE:
        return wanted
    best = max(shares, key=lambda k: shares[k], default=None)
    return best if best is not None and shares[best] >= CLEAR else None
