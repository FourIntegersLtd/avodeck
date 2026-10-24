"""Which quarter turn stores a scanned page the right way up.

Document Intelligence reports each page's `angle`: how far the content is turned clockwise, in degrees,
in (-180, 180]. The page is stored turned the other way, snapped to 0/90/180/270 and kept in the page's
`rotation`, the same stored turn as the reader's "Turn page". A small skew (under `SKEW_DEGREES`) needs no
turn. An angle that is not within `SKEW_DEGREES` of a quarter turn is not trusted and the page is left as
it came. Nothing here reads any page text.
"""

from __future__ import annotations

SKEW_DEGREES = 10.0


def quarter_turn(angle: float) -> int:
    """Clockwise degrees (0, 90, 180 or 270) that put a page whose content is turned `angle` upright."""
    detected = angle % 360
    nearest = round(detected / 90) % 4
    if abs(detected - round(detected / 90) * 90) > SKEW_DEGREES:
        return 0
    return (-nearest * 90) % 360
