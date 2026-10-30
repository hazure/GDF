import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from core.protocol.events import Event

Handler = Callable[..., Awaitable[None]]


class Emitter:
    def __init__(self) -> None:
        self._routes: dict[str, list[Handler]] = {}

    def register(self, event_type: str, handler: Handler) -> None:
        self._routes.setdefault(event_type, []).append(handler)

    def handlers_of(self, event_type: str) -> tuple[Handler, ...]:
        return tuple(self._routes.get(event_type, ()))

    async def dispatch(self, event: Event, bot: Any) -> None:
        for handler in self.handlers_of(event.type):
            try:
                await handler(event, bot)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                logger.opt(exception=True).error(
                    "插件异常已隔离: {}.{} event={}",
                    handler.__module__,
                    handler.__name__,
                    event.type,
                )