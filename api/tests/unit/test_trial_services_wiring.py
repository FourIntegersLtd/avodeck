"""Slice I builds its AI kit and OCR from the cell config like every other slice (`live_or_recorded`): live
clients when the keys are present, with or without a recordings directory, so T-22/T-24 and the bundle
matching work in production."""

from __future__ import annotations

import pytest

from app.core.ai.azure_ocr import AzureOcr
from app.core.ai.bedrock_live import LiveBedrock
from app.features.trial.services import from_env
from tests.support.ai import cell_env
from tests.support.live_ai import AWS_KEYS, DI_KEY


def test_the_cell_keys_give_the_live_kit_and_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_RECORDINGS_DIR", raising=False)
    for name, value in cell_env(**AWS_KEYS, AZURE_DI_KEY=DI_KEY).items():
        monkeypatch.setenv(name, value)
    services = from_env()
    assert services.kit is not None and isinstance(services.kit.bedrock, LiveBedrock)
    assert isinstance(services.ocr, AzureOcr)
    assert services.model() is services.kit


def test_without_keys_or_recordings_the_model_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*AWS_KEYS, "AZURE_DI_KEY", "AI_RECORDINGS_DIR"):
        monkeypatch.delenv(name, raising=False)
    for name, value in cell_env().items():
        monkeypatch.setenv(name, value)
    services = from_env()
    assert services.kit is None and services.ocr is None
