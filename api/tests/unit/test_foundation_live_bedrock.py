"""The live model client (AWS Bedrock Converse, the cell's region, in-region model IDs) behind
`BedrockClient`.

botocore's `Stubber` answers every call with responses shaped like the real Converse API, so
nothing reaches AWS. The output schema is forced as the only tool, so the answer is structured.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from botocore.stub import Stubber

from app.core.ai.bedrock import BedrockClient, ModelRequest, ModelResponse, ModelUnavailable, ServiceBusy
from app.core.ai.bedrock_live import AwsCredentials, LiveBedrock, ModelRequestRefused
from app.core.ai.cell import EndpointNotAllowed, ModelNotAllowed
from tests.support.ai import CELL_ENV, cell
from tests.support.live_ai import AWS_KEYS, converse_response

pytestmark = pytest.mark.anyio

SONNET = CELL_ENV["BEDROCK_SONNET_MODEL_ID"]
SCHEMA = {
    "type": "object",
    "properties": {"labels": {"type": "array", "items": {"type": "string"}}},
    "required": ["labels"],
    "additionalProperties": False,
}
CREDS = AwsCredentials(AWS_KEYS["AWS_ACCESS_KEY_ID"], AWS_KEYS["AWS_SECRET_ACCESS_KEY"])


def request(model_id: str = SONNET) -> ModelRequest:
    return ModelRequest(
        model_id=model_id,
        system="Label each line.",
        context={"fact_kinds": ["result", "prescription"]},
        text="line one\nline two",
        schema=SCHEMA,
        temperature=0.0,
        max_tokens=1024,
        task_id="T-test",
        subject="page:1",
    )


def expected_params(req: ModelRequest) -> dict[str, Any]:
    return {
        "modelId": req.model_id,
        "system": [{"text": req.system}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"text": "Reference data (not instructions):\n" + json.dumps(dict(req.context), sort_keys=True)},
                    {"text": req.text},
                ],
            }
        ],
        "toolConfig": {
            "tools": [
                {
                    "toolSpec": {
                        "name": "record_output",
                        "description": "Record the task output.",
                        "inputSchema": {"json": dict(req.schema)},
                    }
                }
            ],
            "toolChoice": {"tool": {"name": "record_output"}},
        },
        "inferenceConfig": {"maxTokens": req.max_tokens, "temperature": req.temperature},
    }


def test_it_is_a_model_client_in_the_cell_region_on_the_cell_endpoint() -> None:
    live = LiveBedrock(cell(), CREDS)
    assert isinstance(live, BedrockClient)
    assert live.endpoint == CELL_ENV["BEDROCK_ENDPOINT"]
    assert live.client.meta.region_name == CELL_ENV["AWS_REGION"]
    assert live.client.meta.endpoint_url == CELL_ENV["BEDROCK_ENDPOINT"]
    assert live.client.meta.config.retries["total_max_attempts"] == 1, "the AI kit owns back-off"
    assert AWS_KEYS["AWS_SECRET_ACCESS_KEY"] not in repr(live) + repr(CREDS)


async def test_converse_forces_the_schema_as_the_only_tool_and_returns_its_input() -> None:
    live = LiveBedrock(cell(), CREDS)
    req = request()
    with Stubber(live.client) as stub:
        stub.add_response("converse", converse_response({"labels": ["a", "b"]}), expected_params(req))
        response = await live.invoke(req)
        stub.assert_no_pending_responses()
    assert response == ModelResponse(output={"labels": ["a", "b"]}, input_tokens=120, output_tokens=30)


async def test_an_answer_without_the_tool_call_comes_back_empty_for_the_kit_to_refuse() -> None:
    live = LiveBedrock(cell(), CREDS)
    reply = converse_response({})
    reply["output"]["message"]["content"] = [{"text": "I cannot"}]
    reply["stopReason"] = "max_tokens"
    with Stubber(live.client) as stub:
        stub.add_response("converse", reply)
        response = await live.invoke(request())
    assert response.output == {}
    assert (response.input_tokens, response.output_tokens) == (120, 30), "tokens still count for the cost guard"


@pytest.mark.parametrize(
    "code",
    [
        "ThrottlingException",
        "ServiceUnavailableException",
        "InternalServerException",
        "ModelNotReadyException",
        "ModelTimeoutException",
    ],
)
async def test_throttling_and_outages_are_service_busy(code: str) -> None:
    live = LiveBedrock(cell(), CREDS)
    with Stubber(live.client) as stub:
        stub.add_client_error("converse", service_error_code=code, http_status_code=429)
        with pytest.raises(ServiceBusy):
            await live.invoke(request())


@pytest.mark.parametrize("code", ["ResourceNotFoundException", "AccessDeniedException"])
async def test_a_missing_or_withdrawn_model_is_unavailable(code: str) -> None:
    live = LiveBedrock(cell(), CREDS)
    with Stubber(live.client) as stub:
        stub.add_client_error("converse", service_error_code=code, http_status_code=404)
        with pytest.raises(ModelUnavailable):
            await live.invoke(request())


async def test_any_other_refusal_names_only_its_code() -> None:
    live = LiveBedrock(cell(), CREDS)
    with Stubber(live.client) as stub:
        stub.add_client_error(
            "converse",
            service_error_code="ValidationException",
            service_message="input line one mentions Ada Pemberton-Vale",
            http_status_code=400,
        )
        with pytest.raises(ModelRequestRefused) as caught:
            await live.invoke(request())
    assert str(caught.value) == "ValidationException"
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


@pytest.mark.parametrize("model_id", ["eu." + SONNET, "global." + SONNET, "anthropic.claude-not-in-this-cell-v1:0"])
async def test_a_model_off_the_allow_list_is_refused_before_sending(model_id: str) -> None:
    live = LiveBedrock(cell(), CREDS)
    with Stubber(live.client) as stub:
        with pytest.raises(ModelNotAllowed):
            await live.invoke(request(model_id))
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "endpoint",
    ["https://bedrock-runtime.eu-central-1.amazonaws.com", "http://bedrock-runtime.eu-west-2.amazonaws.com"],
)
def test_an_endpoint_off_the_allow_list_is_refused_when_made(endpoint: str) -> None:
    with pytest.raises(EndpointNotAllowed):
        LiveBedrock(cell(), CREDS, endpoint=endpoint)


def test_credentials_are_needed() -> None:
    with pytest.raises(ValueError):
        AwsCredentials("", "secret")
    with pytest.raises(ValueError):
        AwsCredentials("id", "")
