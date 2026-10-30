import argparse
import asyncio
import tomllib
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from core.api.client import ApiClient
from core.bot import Bot, flag_config
from core.connector.ws import WsConnector
from core.console import run_console
from core.emitter import Emitter
from core.loader import load_plugins
from core.logger import setup_logger

CONFIG_PATH = Path(__file__).with_name("config.toml")
LOG_DIR = "logs"
TAIL_LINES = 50
POLL_INTERVAL = 0.5


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="gdf", description="极简 QQ AI 机器人框架")
    parser.add_argument("--log", action="store_true", help="实时查看当天日志")
    parser.add_argument(
        "--no-input", action="store_true", help="无头模式, 不挂载控制台"
    )
    return parser.parse_args()


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("rb") as fp:
        return tomllib.load(fp)


async def run(config: dict[str, Any], with_console: bool) -> None:
    api = ApiClient(
        app_id=str(config["app_id"]),
        app_secret=str(config["app_secret"]),
        sandbox=flag_config(config, "sandbox"),
    )
    bot = Bot(api=api, emitter=Emitter(), config=config)
    load_plugins(bot.emitter)
    ready = asyncio.Event()
    console: asyncio.Task[None] | None = None
    if with_console:
        console = asyncio.create_task(run_console(bot, ready))
    try:
        await WsConnector(bot, ready).run()
    finally:
        if console is not None:
            console.cancel()
            with suppress(asyncio.CancelledError):
                await console
        await api.close()
        if console is None:
            print("机器人已退出", flush=True)


def _log_path() -> Path:
    return Path(LOG_DIR) / f"info_{datetime.now():%Y-%m-%d}.log"


def _read_from(path: Path, offset: int) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as fp:
        fp.seek(offset)
        return fp.read()


async def _print_tail(path: Path, lines: int) -> int:
    text = await asyncio.to_thread(
        path.read_text, encoding="utf-8", errors="replace"
    )
    for line in text.splitlines()[-lines:]:
        print(line, flush=True)
    return len(text.encode("utf-8"))


async def tail_logs(lines: int = TAIL_LINES) -> None:
    path = _log_path()
    if not path.exists():
        print(f"日志文件不存在: {path}", flush=True)
        return
    offset = await _print_tail(path, lines)
    print(f"--- 跟随 {path.name}, Ctrl+C 退出 ---", flush=True)
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        current = _log_path()
        if current != path:
            path = current
            offset = 0
            if not path.exists():
                continue
            print(f"--- 切换到 {path.name} ---", flush=True)
        size = (await asyncio.to_thread(path.stat)).st_size
        if size < offset:
            offset = 0
        if size > offset:
            chunk = await asyncio.to_thread(_read_from, path, offset)
            offset += len(chunk.encode("utf-8"))
            print(chunk, end="", flush=True)


def main() -> None:
    args = _parse_args()
    if args.log:
        try:
            asyncio.run(tail_logs())
        except KeyboardInterrupt:
            print("已退出日志查看", flush=True)
        return
    try:
        config = load_config()
    except Exception:
        setup_logger()
        logger.opt(exception=True).error("配置读取失败")
        raise SystemExit(1)
    setup_logger(config)
    if not config.get("app_id") or not config.get("app_secret"):
        logger.error("config.toml 缺少 app_id 或 app_secret")
        raise SystemExit(1)
    try:
        asyncio.run(run(config, with_console=not args.no_input))
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("收到中断信号, 基础组件已关闭")


if __name__ == "__main__":
    main()