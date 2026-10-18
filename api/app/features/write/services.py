"""What slice E's routes and jobs use from other slices: the B and D ports (`ports.py`).

`current()` gives the stand-ins until something is configured; tests call `configure()` with their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.features.write.ports import AllLinked, FactFlags, NoFlags, QuestionLinks


@dataclass
class WriteServices:
    flags: FactFlags = field(default_factory=NoFlags)
    links: QuestionLinks = field(default_factory=AllLinked)


_current: WriteServices | None = None


def configure(services: WriteServices | None) -> None:
    """Use these services from now on (None: the stand-ins again on next use)."""
    global _current
    _current = services


def current() -> WriteServices:
    global _current
    if _current is None:
        _current = WriteServices()
    return _current
