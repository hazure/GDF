import asyncio
import tomllib
from contextlib import suppress
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


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("rb") as fp:
        return tomllib.load(fp)


async def run(config: dict[str, Any]) -> None:
    api = ApiClient(
        app_id=str(config["app_id"]),
        app_secret=str(config["app_secret"]),
        sandbox=flag_config(config, "sandbox"),
    )
    bot = Bot(api=api, emitter=Emitter(), config=config)
    load_plugins(bot.emitter)
    ready = asyncio.Event()
    console = asyncio.create_task(run_console(bot, ready))
    try:
        await WsConnector(bot, ready).run()
    finally:
        console.cancel()
        with suppress(asyncio.CancelledError):
            await console
        await api.close()


def main() -> None:
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
        asyncio.run(run(config))
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("收到中断信号, 基础组件已关闭")


if __name__ == "__main__":
    main()