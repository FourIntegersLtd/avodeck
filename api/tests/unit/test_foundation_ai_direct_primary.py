"""TEMPORARY, dummy data only (ADR 0036): the Anthropic direct API as the primary model provider.

It is on exactly when `AI_PRIMARY_PROVIDER=anthropic` (and then `ANTHROPIC_API_KEY` is needed). Unset or
`bedrock`, everything is as it was: Bedrock only, the cell's allow-list, `BEDROCK_RPM_LIMIT`. Rule 5 is
knowingly set aside only behind that switch.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app.core.ai.bedrock import RecordedBedrock
from app.core.ai.bedrock_live import LiveBedrock
from app.core.ai.cell import ModelNotAllowed, check_model, model_for
from app.core.ai.clients import live_or_recorded
from app.core.ai.fallback import FallbackClient
from app.core.config import ConfigError, ai_settings
from tests.support.ai import CELL_ENV, cell, cell_env
from tests.support.live_ai import AWS_KEYS

KEY = "sk-ant-test-not-real"
SONNET = CELL_ENV["BEDROCK_SONNET_MODEL_ID"]
DIRECT = "claude-haiku-5-5"
ON = {"AI_PRIMARY_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": KEY}


def test_with_nothing_set_the_settings_are_exactly_todays() -> None:
    settings = cell()
    assert settings.primary_provider == "bedrock"
    assert dict(settings.direct_models) == {}
    assert settings.anthropic_endpoint == ""
    assert settings.pipeline_concurrency == 3
    assert settings.rpm_limit == settings.bedrock_rpm_limit == 5
    assert settings.allowed_hosts == frozenset(CELL_ENV["AI_ENDPOINT_ALLOW_LIST"].split(","))


@pytest.mark.parametrize("value", ["", "  ", "bedrock"])
def test_unset_blank_or_bedrock_is_bedrock_and_a_key_alone_changes_nothing(value: str) -> None:
    settings = ai_settings(cell_env(AI_PRIMARY_PROVIDER=value, ANTHROPIC_API_KEY=KEY))
    assert settings == ai_settings(cell_env())


@pytest.mark.parametrize("value", ["Anthropic", "openai", "direct", "bedrock,anthropic", "1"])
def test_any_other_value_is_refused(value: str) -> None:
    with pytest.raises(ConfigError, match="AI_PRIMARY_PROVIDER"):
        ai_settings(cell_env(AI_PRIMARY_PROVIDER=value, ANTHROPIC_API_KEY=KEY))


@pytest.mark.parametrize("key", [None, "", "   "])
def test_anthropic_without_a_key_is_refused_never_a_silent_fallback(key: str | None) -> None:
    env = cell_env(AI_PRIMARY_PROVIDER="anthropic")
    if key is not None:
        env["ANTHROPIC_API_KEY"] = key
    with pytest.raises(ConfigError, match="AI_PRIMARY_PROVIDER=anthropic needs ANTHROPIC_API_KEY"):
        ai_settings(env)
    with pytest.raises(ConfigError, match="needs ANTHROPIC_API_KEY"):
        live_or_recorded(env)  # not swallowed as "AI not configured"


def test_anthropic_switches_the_primary_and_the_constants_apply() -> None:
    settings = ai_settings(cell_env(**ON))
    assert settings.primary_provider == "anthropic"
    assert dict(settings.direct_models) == {"sonnet": DIRECT}  # no opus: it goes straight to Bedrock
    assert settings.anthropic_endpoint == "https://api.anthropic.com"
    assert "api.anthropic.com" in settings.allowed_hosts
    assert settings.allowed_hosts - {"api.anthropic.com"} == cell().allowed_hosts
    assert settings.anthropic_rpm_limit == 30 and settings.rpm_limit == 30
    assert settings.bedrock_rpm_limit == 5
    assert settings.pipeline_concurrency == 8
    assert settings.models == cell().models  # Bedrock's in-region roles are untouched


def test_switching_back_to_bedrock_restores_everything() -> None:
    assert ai_settings(cell_env(**{**ON, "AI_PRIMARY_PROVIDER": "bedrock"})) == ai_settings(cell_env())


def test_the_key_itself_is_never_stored_in_the_settings() -> None:
    settings = ai_settings(cell_env(**ON))
    assert KEY not in repr(settings) and KEY not in repr(dataclasses.asdict(settings))


def test_bedrock_variables_are_still_required_with_the_switch() -> None:
    env = cell_env(**ON)
    del env["BEDROCK_SONNET_MODEL_ID"]
    with pytest.raises(ConfigError, match="BEDROCK_SONNET_MODEL_ID"):
        ai_settings(env)


def test_a_direct_id_is_refused_while_bedrock_is_primary_and_allowed_when_anthropic_is() -> None:
    with pytest.raises(ModelNotAllowed):
        check_model(cell(), DIRECT)
    direct = ai_settings(cell_env(**ON))
    assert check_model(direct, DIRECT) == DIRECT
    assert check_model(direct, SONNET) == SONNET
    with pytest.raises(ModelNotAllowed):
        check_model(direct, "claude-something-else")
    with pytest.raises(ModelNotAllowed):
        check_model(direct, "global.anthropic.claude-sonnet-5-test-v1:0")


def test_the_hard_coded_direct_id_is_a_plain_claude_model_id_and_not_a_bedrock_one() -> None:
    import re

    from app.core.config import in_region_model_id

    assert re.fullmatch(r"claude-[a-z0-9][a-z0-9.\-]*", DIRECT)
    assert not in_region_model_id(DIRECT)
    assert ai_settings(cell_env(**ON)).direct_models["sonnet"] == DIRECT == "claude-haiku-5-5"


def test_the_kit_still_resolves_the_bedrock_id_for_a_role() -> None:
    assert model_for(ai_settings(cell_env(**ON)), "sonnet") == SONNET


def test_bedrock_primary_gives_the_same_clients_as_before(tmp_path: Path) -> None:
    live = live_or_recorded(cell_env(**AWS_KEYS, ANTHROPIC_API_KEY=KEY))
    assert isinstance(live.bedrock, LiveBedrock)
    recorded = live_or_recorded(cell_env(AI_RECORDINGS_DIR=str(tmp_path), AI_PRIMARY_PROVIDER="bedrock"))
    assert isinstance(recorded.bedrock, RecordedBedrock)
    assert live_or_recorded(cell_env()).bedrock is None


def test_anthropic_puts_the_direct_client_first_over_the_existing_bedrock_client(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")
    clients = live_or_recorded(cell_env(**ON, **AWS_KEYS))
    assert isinstance(clients.bedrock, FallbackClient)
    assert clients.bedrock.endpoint == "https://api.anthropic.com"
    assert isinstance(clients.bedrock.backup, LiveBedrock)
    assert clients.kit is not None and clients.kit.bedrock is clients.bedrock
    assert "AI primary is the direct API: dummy data only (ADR 0036)" in caplog.text
    assert KEY not in caplog.text


def test_anthropic_alone_has_no_backup_and_recordings_become_the_backup(tmp_path: Path) -> None:
    clients = live_or_recorded(cell_env(**ON))
    assert isinstance(clients.bedrock, FallbackClient) and clients.bedrock.backup is None
    recorded = live_or_recorded(cell_env(**ON, AI_RECORDINGS_DIR=str(tmp_path)))
    assert isinstance(recorded.bedrock, FallbackClient)
    assert isinstance(recorded.bedrock.backup, RecordedBedrock)
