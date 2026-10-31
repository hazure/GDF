import asyncio
import json
from pathlib import Path
from typing import Any

from aiohttp import web
from loguru import logger

from core.bot import Bot, flag_config

PLUGIN_DIR = Path(__file__).resolve().parent
DEFAULT_MEMORY_PATH = "data/long_term_memory.json"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
PROTOCOL_VERSION = "2024-11-05"
ENDPOINT = "/mcp"

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "remember",
        "description": "把关于当前用户的一条重要事实或偏好写入其个人档案",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "关于该用户的一条事实、偏好、计划或情绪",
                },
            },
            "required": ["fact"],
        },
    },
    {
        "name": "recall",
        "description": "读取当前用户的个人档案, 返回其历史事实列表",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

_LOCK = asyncio.Lock()
_RUNNER: web.AppRunner | None = None
FACTS_LIMIT = 20

LLM_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["inputSchema"],
        },
    }
    for tool in TOOL_DEFS
]


def _memory_path() -> Path:
    path = Path(DEFAULT_MEMORY_PATH)
    return path if path.is_absolute() else PLUGIN_DIR.parent / path


async def _load() -> dict[str, dict[str, Any]]:
    path = _memory_path()
    if not path.exists():
        return {}
    try:
        raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        logger.opt(exception=True).error("长期记忆文件损坏, 本次按空处理")
        return {}
    return data if isinstance(data, dict) else {}


async def _save(data: dict[str, dict[str, Any]]) -> None:
    path = _memory_path()
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = path.with_name(path.name + ".tmp")
    await asyncio.to_thread(tmp.write_text, text, encoding="utf-8")
    await asyncio.to_thread(tmp.replace, path)


async def _tool_remember(
    user_id: str, name: str, args: dict[str, Any]
) -> dict[str, Any]:
    fact = str(args.get("fact") or "").strip()
    if not fact:
        return {"error": "fact 不能为空"}
    async with _LOCK:
        data = await _load()
        profile = data.get(user_id)
        if not isinstance(profile, dict):
            profile = {"name": "", "facts": []}
        facts = profile.get("facts")
        if not isinstance(facts, list):
            facts = []
        if fact not in facts:
            facts.append(fact)
        profile["facts"] = facts[-FACTS_LIMIT:]
        if name:
            profile["name"] = name
        data[user_id] = profile
        await _save(data)
    return {"ok": True, "user_id": user_id, "name": profile.get("name", ""),
            "fact": fact, "total": len(profile["facts"])}


async def _tool_recall(user_id: str) -> dict[str, Any]:
    async with _LOCK:
        data = await _load()
        profile = data.get(user_id)
    if not isinstance(profile, dict):
        return {"user_id": user_id, "name": "", "facts": []}
    facts = profile.get("facts")
    return {
        "user_id": user_id,
        "name": profile.get("name", ""),
        "facts": facts if isinstance(facts, list) else [],
    }


def _rpc_ok(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_err(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


async def _dispatch(method: str, params: dict[str, Any]) -> Any:
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "gdf-memory", "version": "1.0"},
        }
    if method == "tools/list":
        return {"tools": TOOL_DEFS}
    if method == "tools/call":
        name = str(params.get("name") or "")
        raw_args = params.get("arguments")
        args = dict(raw_args) if isinstance(raw_args, dict) else {}
        user_id = str(params.get("user_id") or "").strip()
        display_name = str(params.get("display_name") or "").strip()
        args.pop("user_id", None)
        args.pop("display_name", None)
        if not user_id:
            return _tool_error("缺少 user_id")
        if name == "remember":
            result = await _tool_remember(user_id, display_name, args)
        elif name == "recall":
            result = await _tool_recall(user_id)
        else:
            return _tool_error(f"未知工具: {name}")
        return {
            "content": [
                {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
            ],
            "isError": "error" in result,
        }
    raise KeyError(method)


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


async def _http_handler(bot: Bot, request: web.Request) -> web.Response:
    token = str(bot.config.get("mcp_token") or "").strip()
    if token and request.headers.get("Authorization", "") != f"Bearer {token}":
        return web.json_response(_rpc_err(None, -32001, "unauthorized"), status=401)
    try:
        payload = await request.json()
    except Exception:
        return web.json_response(_rpc_err(None, -32700, "parse error"))
    if not isinstance(payload, dict):
        return web.json_response(_rpc_err(None, -32600, "invalid request"))
    request_id = payload.get("id")
    method = str(payload.get("method") or "")
    raw_params = payload.get("params")
    params = raw_params if isinstance(raw_params, dict) else {}
    try:
        result = await _dispatch(method, params)
    except KeyError:
        return web.json_response(
            _rpc_err(request_id, -32601, f"method not found: {method}")
        )
    except Exception:
        logger.opt(exception=True).error("MCP 处理失败 method={}", method)
        return web.json_response(_rpc_err(request_id, -32603, "internal error"))
    return web.json_response(_rpc_ok(request_id, result))


async def start(bot: Bot) -> bool:
    global _RUNNER
    if _RUNNER is not None:
        return True
    host = str(bot.config.get("mcp_host") or DEFAULT_HOST)
    port = _int_config(bot, "mcp_port", DEFAULT_PORT)
    app = web.Application()
    app.router.add_post(ENDPOINT, lambda request: _http_handler(bot, request))
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, host, port).start()
    except OSError:
        logger.opt(exception=True).error(
            "MCP 服务启动失败 host={} port={}", host, port
        )
        await runner.cleanup()
        return False
    _RUNNER = runner
    bot.on_shutdown(lambda: stop(bot))
    logger.info("MCP 服务已启动 http://{}:{}{}", host, port, ENDPOINT)
    return True


async def stop(bot: Bot) -> None:
    global _RUNNER
    if _RUNNER is None:
        return
    await _RUNNER.cleanup()
    _RUNNER = None
    logger.info("MCP 服务已关闭")


async def ensure_started(bot: Bot) -> bool:
    if not flag_config(bot.config, "mcp_enabled"):
        return False
    return await start(bot)


def _int_config(bot: Bot, key: str, default: int) -> int:
    try:
        return int(bot.config.get(key, default))
    except (TypeError, ValueError):
        return default