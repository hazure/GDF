import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
from loguru import logger

from core.bot import Bot
from core.protocol.events import C2CMessage, Event, GroupAtMessage

Handler = Callable[..., Awaitable[None]]

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-3.5-turbo"
TIMEOUT = 30.0
FALLBACK = "AI 思考失败，请稍后再试"

_SESSION: aiohttp.ClientSession | None = None
_LOCK = asyncio.Lock()


def on_group_at(func: Handler) -> Handler:
    setattr(func, "__event__", "GROUP_AT_MESSAGE_CREATE")
    return func


def on_c2c(func: Handler) -> Handler:
    setattr(func, "__event__", "C2C_MESSAGE_CREATE")
    return func


@on_group_at
async def llm_group(event: GroupAtMessage, bot: Bot) -> None:
    await _chat(event, bot)


@on_c2c
async def llm_c2c(event: C2CMessage, bot: Bot) -> None:
    await _chat(event, bot)


async def _chat(event: Event, bot: Bot) -> None:
    prompt = event.content.strip()
    if not prompt:
        return
    try:
        reply = await _complete(bot, prompt)
    except Exception:
        logger.opt(exception=True).error("LLM 请求失败 event={}", event.type)
        reply = FALLBACK
    await bot.reply(event, reply)


async def _complete(bot: Bot, prompt: str) -> str:
    api_key = str(bot.config.get("llm_api_key") or "")
    if not api_key:
        raise RuntimeError("config.toml 缺少 llm_api_key")
    base_url = str(bot.config.get("llm_base_url") or DEFAULT_BASE_URL).rstrip("/")
    model = str(bot.config.get("llm_model") or DEFAULT_MODEL)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    session = await _client()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with session.post(
        f"{base_url}/chat/completions", json=payload, headers=headers
    ) as resp:
        data: Any = await resp.json(content_type=None)
        if resp.status >= 400:
            raise RuntimeError(f"LLM status={resp.status} data={data}")
    if not isinstance(data, dict):
        raise RuntimeError(f"LLM 响应非对象: {data}")
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"LLM 响应缺少 choices: {data}")
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip() or FALLBACK


async def _client() -> aiohttp.ClientSession:
    global _SESSION
    async with _LOCK:
        if _SESSION is None or _SESSION.closed:
            _SESSION = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=TIMEOUT)
            )
        return _SESSION