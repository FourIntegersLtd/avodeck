"""Shared pieces for AI kit tests: a cell configuration made of test values, a scripted model and
made-up identifiers. Nothing here is a real endpoint, model or person."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.ai.bedrock import ModelRequest, ModelResponse
    from app.core.config import AiSettings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ai"

# A made-up person; no identifier below belongs to anyone.
NAME = "Ada Pemberton-Vale"

CELL_ENV: dict[str, str] = {
    "REGION": "uk",
    "AWS_REGION": "eu-west-2",
    "BEDROCK_SONNET_MODEL_ID": "anthropic.claude-sonnet-5-test-v1:0",
    "BEDROCK_OPUS_MODEL_ID": "anthropic.claude-opus-5-5-test-v1:0",
    "BEDROCK_ENDPOINT": "https://bedrock-runtime.eu-west-2.amazonaws.com",
    "AZURE_DI_ENDPOINT": "https://avodeck-test-uks.cognitiveservices.azure.com",
    "AZURE_SPEECH_ENDPOINT": "wss://uksouth.stt.speech.microsoft.com",
    "AI_ENDPOINT_ALLOW_LIST": ",".join(
        (
            "bedrock-runtime.eu-west-2.amazonaws.com",
            "avodeck-test-uks.cognitiveservices.azure.com",
            "uksouth.stt.speech.microsoft.com",
        )
    ),
}


def cell_env(**overrides: str) -> dict[str, str]:
    return {**CELL_ENV, **overrides}


def cell(**overrides: str) -> AiSettings:
    from app.core.config import ai_settings

    return ai_settings(cell_env(**overrides))


def mod11_number(first_nine: str) -> str:
    """A ten-digit number whose last digit is a valid modulus-11 check digit (made up for tests)."""
    for prefix in (first_nine, *(f"{int(first_nine) + i:09d}" for i in range(1, 50))):
        total = sum(int(d) * w for d, w in zip(prefix, range(10, 1, -1), strict=True))
        check = (11 - total % 11) % 11
        if check != 10:
            return prefix + str(check)
    raise AssertionError("no valid check digit found")


def spaced(number: str) -> str:
    return f"{number[:3]} {number[3:6]} {number[6:]}"


class ScriptedBedrock:
    """A model stand-in that answers from a script (a response or an exception per call) and keeps
    every request it was sent, so tests can see exactly what would have left the cell."""

    def __init__(self, script: list[ModelResponse | Exception], endpoint: str | None = None) -> None:
        self.script = list(script)
        self.requests: list[ModelRequest] = []
        self.endpoint = endpoint or CELL_ENV["BEDROCK_ENDPOINT"]

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, Exception):
            raise step
        return step


def response(output: Mapping[str, object], input_tokens: int = 100, output_tokens: int = 10) -> ModelResponse:
    from app.core.ai.bedrock import ModelResponse

    return ModelResponse(output=dict(output), input_tokens=input_tokens, output_tokens=output_tokens)
