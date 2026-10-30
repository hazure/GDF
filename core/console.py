import asyncio
import importlib
import os
import signal
import sys
import time
from typing import Any

from loguru import logger

from core.bot import Bot, flag_config
from core.loader import load_plugins, module_names
from core.logger import mute_stderr_info

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
    mute_stderr_info()
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
            _reload(bot)
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
        f" | 沙箱 {flag_config(bot.config, 'sandbox')}",
        flush=True,
    )


def _reload(bot: Bot) -> None:
    task = getattr(bot, "_gc_task", None)
    if task is not None and not task.done():
        task.cancel()
    setattr(bot, "_gc_task", None)
    for name in module_names():
        module: Any = sys.modules.get(name)
        if module is None:
            continue
        try:
            importlib.reload(module)
        except Exception:
            logger.opt(exception=True).error("插件重载失败: {}", name)
    bot.emitter.clear()
    total = load_plugins(bot.emitter)
    print(f"插件已重载, 处理器 {total} 个", flush=True)