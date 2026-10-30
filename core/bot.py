from typing import Any

from core.api.client import ApiClient
from core.emitter import Emitter
from core.protocol.events import C2CMessage, Event, GroupAtMessage


class Bot:
    def __init__(self, api: ApiClient, emitter: Emitter) -> None:
        self.api = api
        self.emitter = emitter

    async def dispatch(self, event: Event) -> None:
        await self.emitter.dispatch(event, self)

    async def reply(self, event: Event, content: str) -> dict[str, Any] | None:
        if isinstance(event, GroupAtMessage):
            return await self.api.post_group_message(
                event.group_openid, content, event.id
            )
        if isinstance(event, C2CMessage):
            return await self.api.post_c2c_message(event.author.id, content, event.id)
        return None