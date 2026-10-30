from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Author:
    id: str = ""
    username: str | None = None
    bot: bool = False

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Author":
        return cls(
            id=str(raw.get("id") or raw.get("member_openid") or ""),
            username=raw.get("username"),
            bot=bool(raw.get("bot", False)),
        )


@dataclass(slots=True)
class Member:
    nick: str | None = None
    roles: tuple[str, ...] = ()

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Member":
        roles = raw.get("roles") or ()
        return cls(nick=raw.get("nick"), roles=tuple(str(role) for role in roles))