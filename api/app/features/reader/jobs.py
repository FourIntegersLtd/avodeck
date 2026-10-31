"""Slice B's jobs are registered by importing their module (the worker loads every feature's `jobs`)."""

from app.features.reader import (
    second_reading,  # noqa: F401  (registers second_reading)
    transcript_jobs,  # noqa: F401  (registers export_transcript)
)
