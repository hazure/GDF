import asyncio
import importlib
import os
import signal
import sys
import time
from typing import Any

from loguru import logger

from core.bot import Bot
from core.emitter import Emitter
from core.loader import load_plugins, module_names

PROMPT = "\nGDF > "

HELP_TEXT = (
    "help    显示本列表\n"
    "status  查看运行时长与处理器数量\n"
    "reload  重新加载插件\n"
    "exit    退出并释放资源"
)


async def run_console(bot: Bot, ready: asyncio.Event) -> None:
    await ready.wait()
    start = time.monotonic()
    print("控制台已就绪, 输入 help 查看指令", flush=True)
    while True:
        try:
            line = await asyncio.to_thread(input, PROMPT)
        except (EOFError, KeyboardInterrupt):
            return
        command = line.strip().lower()
        if not command:
            continue
        if command == "help":
            print(HELP_TEXT, flush=True)
        elif command == "status":
            _status(bot, start)
        elif command == "reload":
            _reload(bot.emitter)
        elif command == "exit":
            print("控制台请求退出", flush=True)
            os.kill(os.getpid(), signal.SIGINT)
            return
        else:
            print(f"未知指令: {command}, 输入 help 查看可用指令", flush=True)


def _status(bot: Bot, start: float) -> None:
    hours, rem = divmod(int(time.monotonic() - start), 3600)
    minutes, seconds = divmod(rem, 60)
    print(
        f"运行 {hours}h{minutes}m{seconds}s | 处理器 {bot.emitter.count()} 个"
        f" | 沙箱 {bool(bot.config.get('sandbox', False))}",
        flush=True,
    )


def _reload(emitter: Emitter) -> None:
    for name in module_names():
        module: Any = sys.modules.get(name)
        if module is None:
            continue
        try:
            importlib.reload(module)
        except Exception:
            logger.opt(exception=True).error("插件重载失败: {}", name)
    emitter.clear()
    total = load_plugins(emitter)
    print(f"插件已重载, 处理器 {total} 个", flush=True)