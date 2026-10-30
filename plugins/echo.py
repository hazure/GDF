from collections.abc import Awaitable, Callable

from core.bot import Bot
from core.protocol.events import GroupAtMessage

Handler = Callable[..., Awaitable[None]]
COMMAND_PREFIX = "echo "


def on_group_at(func: Handler) -> Handler:
    setattr(func, "__event__", "GROUP_AT_MESSAGE_CREATE")
    return func


@on_group_at
async def echo(event: GroupAtMessage, bot: Bot) -> None:
    text = event.content.strip()
    if not text.startswith(COMMAND_PREFIX):
        return
    await bot.reply(event, text[len(COMMAND_PREFIX) :].strip())