import asyncio
import base64
import json
import shutil
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger

from core.bot import Bot, flag_config
from core.protocol.events import C2CMessage, Event, GroupAtMessage
from plugins.mcp_server import LLM_TOOLS, ensure_started

Handler = Callable[..., Awaitable[None]]

PLUGIN_DIR = Path(__file__).resolve().parent
SYSTEM_PATH = PLUGIN_DIR / "prompt_system.txt"
WORLD_PATH = PLUGIN_DIR / "prompt_world.txt"

DEFAULT_HISTORY_PATH = "data/history.json"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TOP_P = 0.9
DEFAULT_MAX_TOKENS = 1024
DEFAULT_CONTEXT_LIMIT = 10
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_CONCURRENCY = 3
DEFAULT_CONCURRENCY_WINDOW = 60.0
DEFAULT_MEMORY_GC_DAYS = 7
DEFAULT_MEMORY_GC_INTERVAL = 86400.0
DEFAULT_REPLY_MODE = "smart"
DEFAULT_WAKE_KEYWORDS = ("诺喵莉", "喵莉")
DEFAULT_EVAL_PROMPT = "你需要判断是否需要回复这句话。只输出 YES 或 NO。"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TIMEOUT = 15.0
MAX_TOOL_ROUNDS = 3
MCP_TIMEOUT = 10.0
DEFAULT_SUBCONSCIOUS_PROMPT = (
    "你是一个后台的潜意识观察者。请阅读以下对话，如果包含关于用户的重要事实、"
    "喜好、计划或情绪（例如：考试、日程、喜好、讨厌的事），请提取出来。"
    "如果没有，返回空字符串。"
)
DEFAULT_SUBCONSCIOUS_WINDOW = 60.0
DEFAULT_SUBCONSCIOUS_MIN_LENGTH = 6
SUBCONSCIOUS_MAX_TOKENS = 50

FALLBACK = "AI 思考失败，请稍后再试"
ECHO_PREFIX = "echo "
PING_COMMAND = "ping"

TOOL_DIRECTIVE = (
    "【系统工具指令】\n"
    "你拥有长期记忆的读写能力。\n"
    "1. 当用户明确要求你「记住」某事，或提到重要的个人信息（如名字、喜好）时，"
    "你必须在回复的同时，通过调用 remember 工具将其存入记忆库。\n"
    "2. 遇到需要回忆用户信息的问题时，必须优先调用 recall 工具查询，"
    "绝不允许凭空捏造。\n"
    "注意：千万不要只口头答应「我记住了」，却不调用工具。"
)

_HISTORY_LOCK = asyncio.Lock()
_RATE_LOCK = asyncio.Lock()
_SYSTEM_CACHE: str | None = None
_RATE: dict[str, list[float]] = {}
_SUB_TASKS: set[asyncio.Task[None]] = set()
_SUB_CLOCK: dict[str, float] = {}


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
    if not prompt and not event.images:
        return
    if not prompt:
        prompt = "[图片]"
    if prompt.startswith(ECHO_PREFIX) or prompt == PING_COMMAND:
        return
    conv_id = _conv_id(event)
    _ensure_gc(bot)
    if not await _should_reply(event, bot, prompt):
        logger.info("唤醒判断未通过，跳过回复 conv={}", conv_id)
        _spawn_subconscious(event, bot, prompt, conv_id)
        return
    if not await _allow(bot, conv_id):
        logger.warning("触发频控, 静默跳过 conv={}", conv_id)
        _spawn_subconscious(event, bot, prompt, conv_id)
        return
    logger.info("收到 [{}] 消息: {}", event.type, prompt[:50])
    limit = _int_config(bot, "llm_context_limit", DEFAULT_CONTEXT_LIMIT)
    async with _HISTORY_LOCK:
        history = await _load(bot)
        turns = _messages_of(history.get(conv_id))
    user_content = await _user_content(bot, prompt, event.images)
    messages = _build_messages(bot, user_content, turns, limit)
    model = str(bot.config.get("llm_model") or DEFAULT_MODEL)
    logger.info("正在请求 LLM, model={}, 图片数={}", model, len(event.images))
    try:
        reply = await _complete(bot, messages, conv_id=conv_id)
    except Exception:
        logger.opt(exception=True).error(
            "LLM 请求失败 conv={} event={}", conv_id, event.type
        )
        await bot.reply(event, FALLBACK)
        return
    logger.info("LLM 回复成功: {}...", reply[:30])
    async with _HISTORY_LOCK:
        history = await _load(bot)
        turns = _messages_of(history.get(conv_id))
        turns.append({"role": "user", "content": prompt})
        turns.append({"role": "assistant", "content": reply})
        history[conv_id] = {
            "last_active": time.time(),
            "messages": turns[-2 * limit :] if limit > 0 else [],
        }
        await _save(bot, history)
    await bot.reply(event, reply)


async def _complete(
    bot: Bot,
    messages: list[dict[str, Any]],
    max_tokens: int | None = None,
    conv_id: str = "",
) -> str:
    if conv_id and await ensure_started(bot):
        return await _request_with_tools(bot, messages, conv_id, max_tokens)
    data = await _post_completion(bot, messages, max_tokens)
    return _extract_text(data)


async def _post_completion(
    bot: Bot,
    messages: list[dict[str, Any]],
    max_tokens: int | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    api_key = str(bot.config.get("llm_api_key") or "")
    if not api_key:
        raise RuntimeError("config.toml 缺少 llm_api_key")
    base_url = str(bot.config.get("llm_base_url") or DEFAULT_BASE_URL).rstrip("/")
    timeout = _float_config(bot, "llm_timeout", DEFAULT_TIMEOUT)
    payload = _payload(bot, messages, max_tokens)
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    session = await bot.api.session()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with session.post(
        f"{base_url}/chat/completions",
        json=payload,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        data: Any = await resp.json(content_type=None)
        if resp.status >= 400:
            logger.error(
                "LLM 请求失败 status={} data={}", resp.status, str(data)[:200]
            )
            raise RuntimeError(f"LLM status={resp.status} body={str(data)[:200]}")
    if not isinstance(data, dict):
        raise RuntimeError(f"LLM 响应非对象: {data}")
    return data


def _extract_text(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"LLM 响应缺少 choices: {data}")
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip() or FALLBACK


async def _request_with_tools(
    bot: Bot,
    messages: list[dict[str, Any]],
    conv_id: str,
    max_tokens: int | None = None,
) -> str:
    working = list(messages)
    for _ in range(MAX_TOOL_ROUNDS):
        data = await _post_completion(bot, working, max_tokens, LLM_TOOLS)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"LLM 响应缺少 choices: {data}")
        message = choices[0].get("message") or {}
        calls = message.get("tool_calls") or []
        if not calls:
            return str(message.get("content") or "").strip() or FALLBACK
        working.append(
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": calls,
            }
        )
        for call in calls:
            working.append(await _exec_tool(bot, conv_id, call))
    raise RuntimeError("工具调用轮次超限")


async def _exec_tool(
    bot: Bot, conv_id: str, call: dict[str, Any]
) -> dict[str, Any]:
    call_id = str(call.get("id") or "")
    function = call.get("function") or {}
    name = str(function.get("name") or "")
    raw = function.get("arguments")
    try:
        arguments = json.loads(raw) if isinstance(raw, str) and raw else {}
    except json.JSONDecodeError:
        arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": call_id or name,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments,
            "user_openid": conv_id,
        },
    }
    result = await _mcp_post(bot, payload)
    logger.info("MCP 调用 {} -> {}", name, str(result)[:120])
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": _tool_text(result),
    }


async def _mcp_post(bot: Bot, payload: dict[str, Any]) -> Any:
    host = str(bot.config.get("mcp_host") or "127.0.0.1")
    port = _int_config(bot, "mcp_port", 8080)
    token = str(bot.config.get("mcp_token") or "").strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    session = await bot.api.session()
    try:
        async with session.post(
            f"http://{host}:{port}/mcp",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=MCP_TIMEOUT),
        ) as resp:
            return await resp.json(content_type=None)
    except Exception:
        logger.opt(exception=True).error(
            "MCP 请求失败 tool={}", (payload.get("params") or {}).get("name")
        )
        return {"error": "MCP 请求失败"}


def _tool_text(result: Any) -> str:
    if isinstance(result, dict):
        body = result.get("result")
        if isinstance(body, dict):
            texts = [
                str(item.get("text"))
                for item in body.get("content") or []
                if isinstance(item, dict) and item.get("text")
            ]
            if texts:
                return "\n".join(texts)
        if "error" in result:
            return json.dumps(result["error"], ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


def _spawn_subconscious(
    event: Event, bot: Bot, prompt: str, conv_id: str
) -> None:
    if not flag_config(bot.config, "llm_subconscious_enabled"):
        return
    if not flag_config(bot.config, "mcp_enabled"):
        return
    min_length = _int_config(
        bot, "llm_subconscious_min_length", DEFAULT_SUBCONSCIOUS_MIN_LENGTH
    )
    if len(prompt) < min_length:
        return
    window = _float_config(
        bot, "llm_subconscious_window", DEFAULT_SUBCONSCIOUS_WINDOW
    )
    now = time.monotonic()
    if now - _SUB_CLOCK.get(conv_id, 0.0) < window:
        return
    _SUB_CLOCK[conv_id] = now
    if not getattr(bot, "_sub_shutdown_registered", False):
        bot.on_shutdown(_cancel_subconscious)
        setattr(bot, "_sub_shutdown_registered", True)
    task = asyncio.create_task(_subconscious_memory(event, bot, prompt, conv_id))
    _SUB_TASKS.add(task)
    task.add_done_callback(_SUB_TASKS.discard)


async def _subconscious_memory(
    event: Event, bot: Bot, prompt: str, conv_id: str
) -> None:
    try:
        system = str(
            bot.config.get("llm_subconscious_prompt") or DEFAULT_SUBCONSCIOUS_PROMPT
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        data = await _post_completion(
            bot, messages, SUBCONSCIOUS_MAX_TOKENS
        )
        finding = _extract_text(data).strip()
        if not finding or finding == FALLBACK:
            return
        await _remember(bot, conv_id, finding)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.opt(exception=True).error("潜意识分析失败 conv={}", conv_id)


async def _remember(bot: Bot, conv_id: str, content: str) -> None:
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": f"sub-{int(time.time() * 1000)}",
        "method": "tools/call",
        "params": {
            "name": "remember",
            "arguments": {
                "key": f"潜意识-{int(time.time())}",
                "content": content,
            },
            "user_openid": conv_id,
        },
    }
    result = await _mcp_post(bot, payload)
    logger.info("潜意识记忆已入库 conv={} -> {}", conv_id, str(result)[:120])


async def _cancel_subconscious() -> None:
    for task in list(_SUB_TASKS):
        task.cancel()
    for task in list(_SUB_TASKS):
        with suppress(asyncio.CancelledError):
            await task
    _SUB_TASKS.clear()


def _payload(
    bot: Bot, messages: list[dict[str, Any]], max_tokens: int | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": str(bot.config.get("llm_model") or DEFAULT_MODEL),
        "messages": messages,
        "stream": False,
        "temperature": _float_config(bot, "llm_temperature", DEFAULT_TEMPERATURE),
        "top_p": _float_config(bot, "llm_top_p", DEFAULT_TOP_P),
        "max_tokens": (
            max_tokens
            if max_tokens is not None
            else _int_config(bot, "llm_max_tokens", DEFAULT_MAX_TOKENS)
        ),
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
    bot: Bot, user_content: Any, turns: list[dict[str, str]], limit: int
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    system = _system_prompt(bot)
    if system:
        messages.append({"role": "system", "content": system})
    if limit > 0:
        messages.extend(turns[-2 * limit :])
    messages.append({"role": "user", "content": user_content})
    return messages


async def _should_reply(event: Event, bot: Bot, prompt: str) -> bool:
    if isinstance(event, C2CMessage):
        return True
    mode = str(bot.config.get("llm_reply_mode") or DEFAULT_REPLY_MODE).strip().lower()
    if mode == "always":
        return True
    if mode == "keyword":
        return _has_keyword(bot, prompt)
    return await _eval_reply(bot, prompt)


def _has_keyword(bot: Bot, prompt: str) -> bool:
    keywords = bot.config.get("llm_wake_keywords")
    if not isinstance(keywords, list):
        keywords = list(DEFAULT_WAKE_KEYWORDS)
    return any(str(word) and str(word) in prompt for word in keywords)


async def _eval_reply(bot: Bot, prompt: str) -> bool:
    system = str(bot.config.get("llm_eval_prompt") or DEFAULT_EVAL_PROMPT)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    try:
        answer = await _complete(bot, messages, max_tokens=8)
    except Exception:
        logger.opt(exception=True).error("唤醒判断失败, 本轮按不回复处理")
        return False
    return answer.strip().upper().startswith("YES")


async def _download_image(bot: Bot, url: str) -> str | None:
    logger.info("开始下载图片: {}", url[:50])
    session = await bot.api.session()
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=IMAGE_TIMEOUT)
        ) as resp:
            if resp.status >= 400:
                logger.warning("图片下载失败 status={}", resp.status)
                return None
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            data = await resp.content.read(MAX_IMAGE_BYTES + 1)
    except Exception:
        logger.opt(exception=True).error("图片下载异常")
        return None
    if not data:
        return None
    if len(data) > MAX_IMAGE_BYTES:
        logger.warning("图片超过 {} 字节上限, 已忽略", MAX_IMAGE_BYTES)
        return None
    logger.info("图片下载完成, 大小: {} 字节", len(data))
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


async def _user_content(bot: Bot, prompt: str, images: list[str]) -> Any:
    if not images or not flag_config(bot.config, "llm_vision_enabled"):
        return prompt
    parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for url in images:
        data_url = await _download_image(bot, url)
        if data_url is None:
            continue
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
    if len(parts) == 1:
        return prompt
    return parts


def _system_prompt(bot: Bot) -> str:
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
        if flag_config(bot.config, "mcp_enabled"):
            parts.append(TOOL_DIRECTIVE)
        _SYSTEM_CACHE = "\n\n".join(parts)
    return _SYSTEM_CACHE


def _conv_id(event: Event) -> str:
    if isinstance(event, GroupAtMessage):
        return f"group:{event.group_openid}"
    if isinstance(event, C2CMessage):
        return f"user:{event.author.id}"
    return f"event:{event.id}"


def _history_path(bot: Bot) -> Path:
    raw = str(bot.config.get("llm_history_path") or DEFAULT_HISTORY_PATH)
    path = Path(raw)
    return path if path.is_absolute() else PLUGIN_DIR.parent / path


def _messages_of(entry: Any) -> list[dict[str, str]]:
    if isinstance(entry, dict):
        messages = entry.get("messages")
        return messages if isinstance(messages, list) else []
    if isinstance(entry, list):
        return entry
    return []


async def _load(bot: Bot) -> dict[str, dict[str, Any]]:
    path = _history_path(bot)
    if not path.exists():
        return {}
    try:
        raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        logger.opt(exception=True).error("历史文件损坏, 本次按空历史处理")
        return {}
    if not isinstance(data, dict):
        return {}
    history: dict[str, dict[str, Any]] = {}
    legacy = False
    for key, value in data.items():
        if isinstance(value, list):
            history[key] = {"last_active": time.time(), "messages": value}
            legacy = True
        elif isinstance(value, dict):
            history[key] = value
    if legacy:
        logger.info("检测到旧格式历史, 已迁移为带时间戳结构")
    return history


async def _save(bot: Bot, history: dict[str, dict[str, Any]]) -> None:
    path = _history_path(bot)
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    await _backup_legacy(path)
    text = json.dumps(history, ensure_ascii=False, indent=2)
    tmp = path.with_name(path.name + ".tmp")
    await asyncio.to_thread(tmp.write_text, text, encoding="utf-8")
    await asyncio.to_thread(tmp.replace, path)


async def _backup_legacy(path: Path) -> None:
    bak = path.with_name(path.name + ".bak")
    if not path.exists() or bak.exists():
        return
    try:
        await asyncio.to_thread(shutil.copy2, path, bak)
        logger.info("已备份历史文件到 {}", bak.name)
    except OSError:
        logger.opt(exception=True).error("历史文件备份失败")


async def _allow(bot: Bot, conv_id: str) -> bool:
    limit = _int_config(bot, "llm_max_concurrency", DEFAULT_MAX_CONCURRENCY)
    window = _float_config(bot, "llm_concurrency_window", DEFAULT_CONCURRENCY_WINDOW)
    if limit <= 0 or window <= 0:
        return True
    now = time.monotonic()
    async with _RATE_LOCK:
        stamps = [stamp for stamp in _RATE.get(conv_id, []) if now - stamp < window]
        if len(stamps) >= limit:
            _RATE[conv_id] = stamps
            return False
        stamps.append(now)
        _RATE[conv_id] = stamps
        return True


def _ensure_gc(bot: Bot) -> None:
    task = getattr(bot, "_gc_task", None)
    if task is None or task.done():
        setattr(bot, "_gc_task", asyncio.create_task(_gc_loop(bot)))


async def _gc_loop(bot: Bot) -> None:
    while True:
        interval = _float_config(
            bot, "llm_memory_gc_interval", DEFAULT_MEMORY_GC_INTERVAL
        )
        if interval <= 0:
            return
        await asyncio.sleep(interval)
        await _gc_once(bot)


async def _gc_once(bot: Bot) -> None:
    days = _int_config(bot, "llm_memory_gc_days", DEFAULT_MEMORY_GC_DAYS)
    if days <= 0:
        return
    cutoff = time.time() - days * 86400
    async with _HISTORY_LOCK:
        history = await _load(bot)
        keep = {
            key: value for key, value in history.items() if _active_at(value) >= cutoff
        }
        removed = len(history) - len(keep)
        if removed <= 0:
            return
        await _save(bot, keep)
    logger.info("记忆 GC 清理 {} 个会话", removed)


def _active_at(value: dict[str, Any]) -> float:
    try:
        return float(value.get("last_active", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


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