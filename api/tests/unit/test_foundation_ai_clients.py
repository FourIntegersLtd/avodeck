"""One factory picks the cell's AI clients (`live_or_recorded`): the live client for each service whose
settings and keys are present, the recorded stand-in when `AI_RECORDINGS_DIR` is set, else none
(the job pauses with `model_unavailable`). Keys come only from the cell config, never from a default
credential chain."""

from __future__ import annotations

from pathlib import Path

from app.core.ai.azure_ocr import AzureOcr
from app.core.ai.bedrock_live import LiveBedrock
from app.core.ai.clients import AiClients, live_or_recorded

from app.core.ai.bedrock import RecordedBedrock
from app.core.ai.kit import AiKit
from app.core.ai.ocr import RecordedOcr
from app.core.ai.recordings import FixtureStore
from app.core.config import AiSettings
from tests.support.ai import cell_env
from tests.support.live_ai import AWS_KEYS, DI_KEY

LIVE = {**AWS_KEYS, "AZURE_DI_KEY": DI_KEY}


def test_keys_present_give_the_live_clients_and_a_kit_over_them() -> None:
    clients = live_or_recorded(cell_env(**LIVE))
    assert isinstance(clients, AiClients)
    assert isinstance(clients.bedrock, LiveBedrock)
    assert isinstance(clients.ocr, AzureOcr)
    kit = clients.kit
    assert isinstance(kit, AiKit) and kit.bedrock is clients.bedrock


def test_live_wins_over_recordings_when_both_are_set(tmp_path: Path) -> None:
    clients = live_or_recorded(cell_env(**LIVE, AI_RECORDINGS_DIR=str(tmp_path)))
    assert isinstance(clients.bedrock, LiveBedrock) and isinstance(clients.ocr, AzureOcr)


def test_recordings_without_keys_give_the_stand_ins(tmp_path: Path) -> None:
    clients = live_or_recorded(cell_env(AI_RECORDINGS_DIR=str(tmp_path)))
    assert isinstance(clients.bedrock, RecordedBedrock)
    assert isinstance(clients.ocr, RecordedOcr)
    assert clients.kit is not None


def test_each_service_is_chosen_on_its_own_keys(tmp_path: Path) -> None:
    ocr_only = live_or_recorded(cell_env(AZURE_DI_KEY=DI_KEY, AI_RECORDINGS_DIR=str(tmp_path)))
    assert isinstance(ocr_only.ocr, AzureOcr) and isinstance(ocr_only.bedrock, RecordedBedrock)
    model_only = live_or_recorded(cell_env(**AWS_KEYS))
    assert isinstance(model_only.bedrock, LiveBedrock) and model_only.ocr is None


def test_half_a_pair_of_aws_keys_is_not_live() -> None:
    clients = live_or_recorded(cell_env(AWS_ACCESS_KEY_ID=AWS_KEYS["AWS_ACCESS_KEY_ID"]))
    assert clients.bedrock is None and clients.kit is None


def test_nothing_without_keys_or_recordings() -> None:
    clients = live_or_recorded(cell_env())
    assert clients.bedrock is None and clients.ocr is None and clients.kit is None
    assert isinstance(clients.settings, AiSettings)


def test_nothing_without_the_cell_settings_even_with_keys(tmp_path: Path) -> None:
    clients = live_or_recorded({**LIVE, "AI_RECORDINGS_DIR": str(tmp_path)})
    assert clients.settings is None and clients.bedrock is None and clients.ocr is None and clients.kit is None


def test_the_recorded_model_can_be_swapped_by_the_caller(tmp_path: Path) -> None:
    made: list[tuple[AiSettings, FixtureStore]] = []

    def fake(settings: AiSettings, store: FixtureStore) -> RecordedBedrock:
        made.append((settings, store))
        return RecordedBedrock(settings, store)

    clients = live_or_recorded(cell_env(AI_RECORDINGS_DIR=str(tmp_path)), recorded_bedrock=fake)
    assert len(made) == 1 and clients.bedrock is not None
    assert made[0][1].root == tmp_path
