from collections.abc import Awaitable, Callable

from core.bot import Bot
from core.protocol.events import C2CMessage

Handler = Callable[..., Awaitable[None]]


def on_c2c(func: Handler) -> Handler:
    setattr(func, "__event__", "C2C_MESSAGE_CREATE")
    return func


@on_c2c
async def ping(event: C2CMessage, bot: Bot) -> None:
    await bot.reply(event, "pong")