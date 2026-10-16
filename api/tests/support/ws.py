"""A small WebSocket client that talks to the ASGI app directly (httpx has no WebSocket support).

It runs the app in the test's own event loop, so the app's database pool works as it does for HTTP
tests. `ws.accepted` is False when the server closed the connection before accepting it; then
`ws.close_code` says why.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message


class WebSocketClosed(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"closed with {code}")
        self.code = code


class WsClient:
    def __init__(self) -> None:
        self.to_app: asyncio.Queue[Message] = asyncio.Queue()
        self.from_app: asyncio.Queue[Message] = asyncio.Queue()
        self.accepted = False
        self.close_code: int | None = None
        self.received: list[Any] = []

    async def send_bytes(self, data: bytes) -> None:
        await self.to_app.put({"type": "websocket.receive", "bytes": data})

    async def send_json(self, data: Any) -> None:
        await self.to_app.put({"type": "websocket.receive", "text": json.dumps(data)})

    async def receive_json(self, timeout: float = 10.0) -> Any:
        message = await asyncio.wait_for(self.from_app.get(), timeout)
        if message["type"] == "websocket.close":
            self.close_code = int(message.get("code", 1000))
            raise WebSocketClosed(self.close_code)
        text = message.get("text")
        value = json.loads(text if text is not None else message["bytes"])
        self.received.append(value)
        return value

    async def receive_until_closed(self, timeout: float = 10.0) -> list[Any]:
        """Every message the server sends until it closes the connection."""
        values = []
        while True:
            try:
                values.append(await self.receive_json(timeout))
            except WebSocketClosed:
                return values


@asynccontextmanager
async def ws_connect(app: ASGIApp, url: str, headers: Sequence[tuple[str, str]] = ()) -> AsyncIterator[WsClient]:
    parts = urlsplit(url)
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "wss",
        "server": ("test", 443),
        "client": ("127.0.0.1", 50000),
        "root_path": "",
        "path": parts.path,
        "raw_path": parts.path.encode(),
        "query_string": parts.query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in [("host", "test"), *headers]],
        "subprotocols": [],
        "state": {},
    }
    ws = WsClient()

    async def receive() -> Message:
        return await ws.to_app.get()

    async def send(message: Message) -> None:
        await ws.from_app.put(message)

    await ws.to_app.put({"type": "websocket.connect"})

    async def serve() -> None:
        await app(scope, receive, send)

    task: asyncio.Task[None] = asyncio.create_task(serve())
    first = await asyncio.wait_for(ws.from_app.get(), 10)
    if first["type"] == "websocket.accept":
        ws.accepted = True
    elif first["type"] == "websocket.close":
        ws.close_code = int(first.get("code", 1000))
    else:  # an HTTP denial response
        ws.close_code = 1008
    try:
        yield ws
    finally:
        await ws.to_app.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(task, 10)
        except Exception:  # noqa: BLE001 - the app's own errors are not the test's subject here
            task.cancel()
