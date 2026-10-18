"""The cell's AI clients, chosen once from the cell config: `live_or_recorded()`.

For each service on its own: the live client when the cell settings (`ai_settings`) and that
service's keys are present (Bedrock: `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`, optional
`AWS_SESSION_TOKEN`; OCR: `AZURE_DI_KEY`); else the recorded stand-in when `AI_RECORDINGS_DIR` names
recordings; else none, and a job that needs it pauses with `model_unavailable`. Keys are read only
from the cell config, never from an SDK's default credential chain, so a developer's own cloud
profile can never switch a test or local run to live.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from app.core.ai.bedrock import BedrockClient, RecordedBedrock
from app.core.ai.kit import AiKit
from app.core.ai.ocr import OcrClient, RecordedOcr
from app.core.ai.recordings import FixtureStore
from app.core.config import AiSettings, ConfigError, ai_settings


@dataclass(frozen=True)
class AiClients:
    settings: AiSettings | None
    bedrock: BedrockClient | None
    ocr: OcrClient | None

    @property
    def kit(self) -> AiKit | None:
        if self.settings is None or self.bedrock is None:
            return None
        return AiKit(self.settings, self.bedrock)


def live_or_recorded(
    env: Mapping[str, str] | None = None,
    *,
    recorded_bedrock: Callable[[AiSettings, FixtureStore], BedrockClient] = RecordedBedrock,
    recorded_ocr: Callable[[AiSettings, FixtureStore], OcrClient] = RecordedOcr,
) -> AiClients:
    source = os.environ if env is None else env
    try:
        settings = ai_settings(source)
    except ConfigError:
        return AiClients(settings=None, bedrock=None, ocr=None)

    def value(name: str) -> str:
        return source.get(name, "").strip()

    recordings = value("AI_RECORDINGS_DIR")
    store = FixtureStore(Path(recordings)) if recordings else None

    bedrock: BedrockClient | None = None
    if value("AWS_ACCESS_KEY_ID") and value("AWS_SECRET_ACCESS_KEY"):
        from app.core.ai.bedrock_live import AwsCredentials, LiveBedrock

        credentials = AwsCredentials(
            value("AWS_ACCESS_KEY_ID"), value("AWS_SECRET_ACCESS_KEY"), value("AWS_SESSION_TOKEN") or None
        )
        bedrock = LiveBedrock(settings, credentials)
    elif store is not None:
        bedrock = recorded_bedrock(settings, store)

    ocr: OcrClient | None = None
    if value("AZURE_DI_KEY"):
        from app.core.ai.azure_ocr import AzureOcr

        ocr = AzureOcr(settings, value("AZURE_DI_KEY"))
    elif store is not None:
        ocr = recorded_ocr(settings, store)

    return AiClients(settings=settings, bedrock=bedrock, ocr=ocr)
