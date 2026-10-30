import asyncio
import json
from contextlib import suppress
from typing import Any

import aiohttp
from loguru import logger

from core.bot import Bot
from core.protocol.events import parse_event

INTENTS: int = 1 << 25

OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10

INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 60.0
HELLO_TIMEOUT = 15.0
DEFAULT_HEARTBEAT_MS = 30000.0


class WsConnector:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def run(self) -> None:
        backoff = INITIAL_BACKOFF
        while True:
            try:
                await self._connect_once()
                logger.warning("WS 连接已关闭, {} 秒后重连", backoff)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                logger.opt(exception=True).error("WS 连接异常, {} 秒后重连", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

    async def _connect_once(self) -> None:
        gateway = await self._bot.api.request("GET", "/gateway")
        url = gateway.get("url")
        if not url:
            raise RuntimeError(f"gateway 响应缺少 url: {gateway}")
        identify = _identify_payload(await self._bot.api.token())
        session = aiohttp.ClientSession()
        try:
            async with session.ws_connect(str(url)) as ws:
                await self._session_loop(ws, identify)
        finally:
            await session.close()

    async def _session_loop(
        self, ws: aiohttp.ClientWebSocketResponse, identify: dict[str, Any]
    ) -> None:
        interval = await self._hello_interval(ws)
        await ws.send_json(identify)
        heartbeat = asyncio.create_task(self._heartbeat_loop(ws, interval))
        try:
            async for msg in ws:
                if msg.type is aiohttp.WSMsgType.TEXT:
                    await self._handle_text(msg.data)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _handle_text(self, raw: str) -> None:
        payload: dict[str, Any] = json.loads(raw)
        op = payload.get("op")
        if op == OP_DISPATCH:
            event = parse_event(payload)
            if event is not None:
                await self._bot.dispatch(event)
        elif op in (OP_RECONNECT, OP_INVALID_SESSION):
            raise RuntimeError(f"服务端要求重建连接 op={op}")

    async def _hello_interval(self, ws: aiohttp.ClientWebSocketResponse) -> float:
        async with asyncio.timeout(HELLO_TIMEOUT):
            async for msg in ws:
                if msg.type is aiohttp.WSMsgType.TEXT:
                    payload: dict[str, Any] = json.loads(msg.data)
                    if payload.get("op") == OP_HELLO:
                        data = payload.get("d") or {}
                        interval_ms = float(
                            data.get("heartbeat_interval", DEFAULT_HEARTBEAT_MS)
                        )
                        return interval_ms / 1000.0
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
        raise RuntimeError("未收到 op=10 Hello")

    async def _heartbeat_loop(
        self, ws: aiohttp.ClientWebSocketResponse, interval: float
    ) -> None:
        while True:
            await asyncio.sleep(interval)
            await ws.send_json({"op": OP_HEARTBEAT, "d": None})


def _identify_payload(token: str) -> dict[str, Any]:
    return {
        "op": OP_IDENTIFY,
        "d": {
            "token": f"QQBot {token}",
            "intents": INTENTS,
            "shard": [0, 1],
            "properties": {},
        },
    }