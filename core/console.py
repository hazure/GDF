import asyncio
import importlib
import json
import os
import signal
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

from loguru import logger
from prompt_toolkit import Application, PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    Float,
    FloatContainer,
    FormattedTextControl,
    HSplit,
    Layout,
    Window,
)
from prompt_toolkit.layout.containers import ConditionalContainer
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.styles import Style

from core.bot import Bot
from core.emitter import Emitter
from core.loader import load_plugins, module_names
from core.logger import pause_stderr, resume_stderr, set_redraw_handler

PROMPT = "GDF > "

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config.toml"
MANUAL_DIR = ROOT_DIR / "docs" / "config_manual"
VISIBLE_ROWS = 17
SECRET_KEYS = ("app_secret", "llm_api_key")

EDIT_STYLE = Style.from_dict(
    {
        "active": "reverse",
        "help": "bg:#222222 #ffffff",
        "status": "bg:#333333 #dddddd",
    }
)

HELP_TEXT = (
    "help    显示本列表\n"
    "status  查看运行时长与处理器数量\n"
    "reload  重新加载插件\n"
    "edit    打开配置编辑器\n"
    "exit    退出并释放资源"
)


async def run_console(bot: Bot, ready: asyncio.Event) -> None:
    await ready.wait()
    start = time.monotonic()
    session = PromptSession()
    set_redraw_handler(_redraw_log)
    logger.info("控制台已就绪, 输入 help 查看指令")
    while True:
        try:
            line = await session.prompt_async(PROMPT)
        except (EOFError, KeyboardInterrupt):
            set_redraw_handler(None)
            return
        command = line.strip().lower()
        if not command:
            continue
        if command == "help":
            logger.info("可用指令:\n{}", HELP_TEXT)
        elif command == "status":
            _status(bot, start)
        elif command == "reload":
            _reload(bot.emitter)
        elif command == "edit":
            await _run_edit()
        elif command == "exit":
            logger.info("控制台请求退出")
            set_redraw_handler(None)
            os.kill(os.getpid(), signal.SIGINT)
            return
        else:
            logger.error("未知指令: {}, 输入 help 查看可用指令", command)


def _redraw_log(text: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        sys.stderr.write(text)
        sys.stderr.flush()
        return
    loop.create_task(_write_through_prompt(text))


async def _write_through_prompt(text: str) -> None:
    try:
        async with run_in_terminal():
            sys.stderr.write(text)
            sys.stderr.flush()
    except Exception:
        sys.stderr.write(text)
        sys.stderr.flush()


def _status(bot: Bot, start: float) -> None:
    hours, rem = divmod(int(time.monotonic() - start), 3600)
    minutes, seconds = divmod(rem, 60)
    logger.info(
        "运行 {}h{}m{}s | 处理器 {} 个 | 沙箱 {}",
        hours,
        minutes,
        seconds,
        bot.emitter.count(),
        bool(bot.config.get("sandbox", False)),
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
    logger.success("插件已重载, 处理器 {} 个", total)


class _ConfigEditor:
    def __init__(self, values: dict[str, Any], manual: dict[str, dict[str, str]]) -> None:
        self.items: list[tuple[str, Any]] = list(values.items())
        self.manual = manual
        self.index = 0
        self.editing = False
        self.help_visible = False
        self.accepted = False
        self.error = ""
        self.buffer = Buffer(name="value")

    def _key(self) -> str:
        return self.items[self.index][0]

    def _display(self, key: str, value: Any) -> str:
        if key in SECRET_KEYS:
            return "****"
        return _format_value(value)

    def _render_items(self) -> list[tuple[str, str]]:
        start = max(0, self.index - VISIBLE_ROWS // 2)
        end = min(len(self.items), start + VISIBLE_ROWS)
        rows: list[tuple[str, str]] = []
        for position in range(start, end):
            key, value = self.items[position]
            brief = self.manual.get(key, {}).get("brief", "")
            mark = ">" if position == self.index else " "
            style = "class:active" if position == self.index else ""
            rows.append((style, f"{mark} {key} = {self._display(key, value)}\n"))
            rows.append(("", f"    {brief}\n"))
        return rows

    def _render_help(self) -> str:
        key = self._key()
        full = self.manual.get(key, {}).get("full", "无说明")
        return f"{key}\n\n{full}\n\n按 Esc 关闭"

    def _render_status(self) -> str:
        if self.editing:
            return f"编辑 {self._key()}: {self.error}"
        return "Up/Down 移动  Enter 编辑  Tab 说明  Ctrl+S 保存  Esc 退出"

    def _render_hint(self) -> str:
        return f"输入 {self._key()} 的新值, Enter 确认"

    def _start_edit(self) -> None:
        self.buffer.text = str(self.items[self.index][1])
        self.buffer.cursor_position = len(self.buffer.text)
        self.error = ""
        self.editing = True

    def _commit_edit(self) -> None:
        key, current = self.items[self.index]
        try:
            value = _coerce(current, self.buffer.text)
        except (TypeError, ValueError):
            self.error = "类型不匹配, 未修改"
            self.editing = False
            return
        self.items[self.index] = (key, value)
        self.editing = False

    def values(self) -> dict[str, Any]:
        return dict(self.items)

    def build(self) -> Application[None]:
        not_editing = Condition(lambda: not self.editing)
        editing = Condition(lambda: self.editing)
        help_visible = Condition(lambda: self.help_visible)
        keys = KeyBindings()

        @keys.add("up", filter=not_editing)
        def _up(event: Any) -> None:
            self.index = max(0, self.index - 1)

        @keys.add("down", filter=not_editing)
        def _down(event: Any) -> None:
            self.index = min(len(self.items) - 1, self.index + 1)

        @keys.add("enter", filter=not_editing)
        def _enter(event: Any) -> None:
            self._start_edit()
            event.app.layout.focus(self.buffer)

        @keys.add("enter", filter=editing)
        def _confirm(event: Any) -> None:
            self._commit_edit()

        @keys.add("tab")
        def _help(event: Any) -> None:
            self.help_visible = not self.help_visible

        @keys.add("escape")
        def _escape(event: Any) -> None:
            if self.help_visible:
                self.help_visible = False
            elif self.editing:
                self.editing = False
            else:
                event.app.exit()

        @keys.add("c-s")
        def _save(event: Any) -> None:
            self.accepted = True
            event.app.exit()

        @keys.add("c-c")
        def _cancel(event: Any) -> None:
            self.accepted = False
            event.app.exit()

        body = HSplit(
            [
                Window(
                    FormattedTextControl(self._render_items),
                    height=Dimension(weight=1),
                ),
                ConditionalContainer(
                    Window(FormattedTextControl(self._render_hint), height=1),
                    filter=editing,
                ),
                ConditionalContainer(
                    Window(BufferControl(self.buffer), height=1),
                    filter=editing,
                ),
                Window(
                    FormattedTextControl(self._render_status),
                    height=1,
                    style="class:status",
                ),
            ]
        )
        root = FloatContainer(
            content=body,
            floats=[
                Float(
                    content=ConditionalContainer(
                        Window(
                            FormattedTextControl(self._render_help),
                            style="class:help",
                            height=Dimension(min=4, max=14),
                        ),
                        filter=help_visible,
                    ),
                    top=2,
                    left=2,
                    right=2,
                )
            ],
        )
        return Application(
            layout=Layout(root),
            key_bindings=keys,
            style=EDIT_STYLE,
            full_screen=False,
            mouse_support=False,
        )


async def _run_edit() -> None:
    editor = _ConfigEditor(_read_config(), _load_manual())
    app = editor.build()
    pause_stderr()
    try:
        await app.run_async()
    except Exception:
        resume_stderr()
        logger.opt(exception=True).error("TUI 崩溃")
        return
    resume_stderr()
    if not editor.accepted:
        logger.info("已取消, 配置未改动")
        return
    try:
        await asyncio.to_thread(_write_config, editor.values())
    except OSError:
        logger.opt(exception=True).error("写入 config.toml 失败")
        return
    logger.success("保存成功, 输入 reload 生效")
    logger.warning("日志目录与保留期需重启进程才生效")


def _read_config() -> dict[str, Any]:
    with CONFIG_PATH.open("rb") as fp:
        return tomllib.load(fp)


def _load_manual() -> dict[str, dict[str, str]]:
    manual: dict[str, dict[str, str]] = {}
    if not MANUAL_DIR.exists():
        return manual
    for path in sorted(MANUAL_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        manual[path.stem] = {
            "brief": str(data.get("brief", "")),
            "full": str(data.get("full", "")),
        }
    return manual


def _coerce(current: Any, text: str) -> Any:
    if isinstance(current, bool):
        return text.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(text.strip())
    if isinstance(current, float):
        return float(text.strip())
    return text


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_config(values: dict[str, Any]) -> None:
    lines = (
        CONFIG_PATH.read_text(encoding="utf-8").splitlines()
        if CONFIG_PATH.exists()
        else []
    )
    written: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in values:
                out.append(f"{key} = {_format_value(values[key])}")
                written.add(key)
                continue
        out.append(line)
    for key, value in values.items():
        if key not in written:
            out.append(f"{key} = {_format_value(value)}")
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(CONFIG_PATH)