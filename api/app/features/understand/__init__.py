"""Slice D, understand (docs/slices/D-understand.md). Owned by understand-owner.

Timeline (S11), one consultation as the clinician saw it (S12) and things to look at (S13). The
engine modules (`lanes`, `availability`, `as_seen`) are plain code over pack-shaped data, so every
lane, kind and check comes from the case's packs (rule 11).
"""

from __future__ import annotations
