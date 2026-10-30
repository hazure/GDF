import importlib
import pkgutil
from types import ModuleType
from typing import Any

from loguru import logger

from core.emitter import Emitter

PLUGIN_PACKAGE = "plugins"
EVENT_ATTR = "__event__"


def module_names(package: str = PLUGIN_PACKAGE) -> list[str]:
    pkg = importlib.import_module(package)
    paths: Any = getattr(pkg, "__path__", None)
    if paths is None:
        return []
    return [f"{package}.{info.name}" for info in pkgutil.iter_modules(paths)]


def load_plugins(emitter: Emitter, package: str = PLUGIN_PACKAGE) -> int:
    total = 0
    for name in module_names(package):
        try:
            module = importlib.import_module(name)
        except Exception:
            logger.opt(exception=True).error("插件导入失败: {}", name)
            continue
        total += register_module(module, emitter)
    logger.info("插件处理器加载完成, 共 {} 个", total)
    return total


def register_module(module: ModuleType, emitter: Emitter) -> int:
    count = 0
    for attr in dir(module):
        handler: Any = getattr(module, attr)
        event_type = getattr(handler, EVENT_ATTR, None)
        if event_type is None or not callable(handler):
            continue
        emitter.register(str(event_type), handler)
        count += 1
    return count