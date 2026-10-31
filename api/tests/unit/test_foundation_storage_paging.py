"""Storage listing pages through every result, and a missing object is its own error (stream A)."""

from __future__ import annotations

import httpx
import pytest

from app.core.storage import StorageError, StorageNotFound, StorageSettings, SupabaseStorage

pytestmark = pytest.mark.anyio


def storage_with(handler: httpx.MockTransport, page: int = 3) -> SupabaseStorage:
    storage = SupabaseStorage(StorageSettings(url="http://storage.test", service_role_key="k"), list_page_size=page)
    storage._client = httpx.AsyncClient(base_url="http://storage.test/storage/v1", transport=handler)
    return storage


async def test_list_reads_every_page_not_just_the_first() -> None:
    names = [f"{n:05d}" for n in range(1, 8)]
    seen: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        seen.append(body["offset"])
        page = names[body["offset"] : body["offset"] + body["limit"]]
        return httpx.Response(200, json=[{"name": n, "id": f"id-{n}"} for n in page])

    storage = storage_with(httpx.MockTransport(handle))
    assert await storage.list("cases/x/uploads/y/") == [f"cases/x/uploads/y/{n}" for n in names]
    assert seen == [0, 3, 6]


@pytest.mark.parametrize(
    ("status", "body"), [(404, {"error": "not_found"}), (400, {"statusCode": "404", "error": "not_found"})]
)
async def test_a_missing_object_is_storage_not_found(status: int, body: dict[str, str]) -> None:
    storage = storage_with(httpx.MockTransport(lambda request: httpx.Response(status, json=body)))
    with pytest.raises(StorageNotFound):
        await storage.get("a/b")


async def test_other_failures_are_plain_storage_errors() -> None:
    storage = storage_with(httpx.MockTransport(lambda request: httpx.Response(503, text="down")))
    with pytest.raises(StorageError) as err:
        await storage.get("a/b")
    assert not isinstance(err.value, StorageNotFound)
