import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger

from core.bot import Bot
from core.protocol.events import C2CMessage, Event, GroupAtMessage

Handler = Callable[..., Awaitable[None]]

PLUGIN_DIR = Path(__file__).resolve().parent
DATA_DIR = PLUGIN_DIR.parent / "data"
HISTORY_PATH = DATA_DIR / "history.json"
SYSTEM_PATH = PLUGIN_DIR / "prompt_system.txt"
WORLD_PATH = PLUGIN_DIR / "prompt_world.txt"

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TOP_P = 0.9
DEFAULT_MAX_TOKENS = 1024
DEFAULT_CONTEXT_LIMIT = 10

TIMEOUT = 30.0
FALLBACK = "AI 思考失败，请稍后再试"
ECHO_PREFIX = "echo "
PING_COMMAND = "ping"

_HISTORY_LOCK = asyncio.Lock()
_SYSTEM_CACHE: str | None = None


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
    if not prompt or prompt.startswith(ECHO_PREFIX) or prompt == PING_COMMAND:
        return
    conv_id = _conv_id(event)
    limit = _int_config(bot, "llm_context_limit", DEFAULT_CONTEXT_LIMIT)
    async with _HISTORY_LOCK:
        history = await _load()
        turns = list(history.get(conv_id, []))
    messages = _build_messages(prompt, turns, limit)
    try:
        reply = await _complete(bot, messages)
    except Exception:
        logger.opt(exception=True).error(
            "LLM 请求失败 conv={} event={}", conv_id, event.type
        )
        await bot.reply(event, FALLBACK)
        return
    async with _HISTORY_LOCK:
        history = await _load()
        turns = history.get(conv_id, [])
        turns.append({"role": "user", "content": prompt})
        turns.append({"role": "assistant", "content": reply})
        history[conv_id] = turns[-2 * limit :] if limit > 0 else []
        await _save(history)
    await bot.reply(event, reply)


async def _complete(bot: Bot, messages: list[dict[str, str]]) -> str:
    api_key = str(bot.config.get("llm_api_key") or "")
    if not api_key:
        raise RuntimeError("config.toml 缺少 llm_api_key")
    base_url = str(bot.config.get("llm_base_url") or DEFAULT_BASE_URL).rstrip("/")
    payload = _payload(bot, messages)
    logger.debug("LLM payload: {}", json.dumps(payload, ensure_ascii=False)[:300])
    session = await bot.api.session()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with session.post(
        f"{base_url}/chat/completions",
        json=payload,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=TIMEOUT),
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


def _payload(bot: Bot, messages: list[dict[str, str]]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": str(bot.config.get("llm_model") or DEFAULT_MODEL),
        "messages": messages,
        "stream": False,
        "temperature": _float_config(bot, "llm_temperature", DEFAULT_TEMPERATURE),
        "top_p": _float_config(bot, "llm_top_p", DEFAULT_TOP_P),
        "max_tokens": _int_config(bot, "llm_max_tokens", DEFAULT_MAX_TOKENS),
    }
    thinking = str(bot.config.get("llm_thinking") or "").strip()
    if thinking:
        payload["thinking"] = {"type": thinking}
    effort = str(bot.config.get("llm_reasoning_effort") or "").strip()
    if effort:
        payload["reasoning_effort"] = effort
    if _flag_config(bot, "llm_json_mode"):
        payload["response_format"] = {"type": "json_object"}
    if _flag_config(bot, "llm_logprobs"):
        payload["logprobs"] = True
    return payload


def _build_messages(
    prompt: str, turns: list[dict[str, str]], limit: int
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    system = _system_prompt()
    if system:
        messages.append({"role": "system", "content": system})
    if limit > 0:
        messages.extend(turns[-2 * limit :])
    messages.append({"role": "user", "content": prompt})
    return messages


def _system_prompt() -> str:
    global _SYSTEM_CACHE
    if _SYSTEM_CACHE is None:
        parts: list[str] = []
        for path in (SYSTEM_PATH, WORLD_PATH):
            try:
                text = path.read_text(encoding="utf-8").strip()
            except OSError:
                logger.warning("提示词文件缺失: {}", path.name)
                continue
            if text:
                parts.append(text)
        _SYSTEM_CACHE = "\n\n".join(parts)
    return _SYSTEM_CACHE


def _conv_id(event: Event) -> str:
    if isinstance(event, GroupAtMessage):
        return f"group:{event.group_openid}"
    if isinstance(event, C2CMessage):
        return f"user:{event.author.id}"
    return f"event:{event.id}"


async def _load() -> dict[str, list[dict[str, str]]]:
    if not HISTORY_PATH.exists():
        return {}
    try:
        raw = await asyncio.to_thread(HISTORY_PATH.read_text, encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        logger.opt(exception=True).error("历史文件损坏, 本次按空历史处理")
        return {}
    return data if isinstance(data, dict) else {}


async def _save(history: dict[str, list[dict[str, str]]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(history, ensure_ascii=False, indent=2)
    tmp = HISTORY_PATH.with_name(HISTORY_PATH.name + ".tmp")
    await asyncio.to_thread(tmp.write_text, text, encoding="utf-8")
    await asyncio.to_thread(tmp.replace, HISTORY_PATH)


def _float_config(bot: Bot, key: str, default: float) -> float:
    try:
        return float(bot.config.get(key, default))
    except (TypeError, ValueError):
        return default


def _int_config(bot: Bot, key: str, default: int) -> int:
    try:
        return int(bot.config.get(key, default))
    except (TypeError, ValueError):
        return default


def _flag_config(bot: Bot, key: str) -> bool:
    value = bot.config.get(key, False)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)