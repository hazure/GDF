from dataclasses import dataclass
from typing import Any

from core.protocol.models import Author, Member

GROUP_AT_MESSAGE_CREATE = "GROUP_AT_MESSAGE_CREATE"
GROUP_MESSAGE_CREATE = "GROUP_MESSAGE_CREATE"
C2C_MESSAGE_CREATE = "C2C_MESSAGE_CREATE"


@dataclass(slots=True)
class Event:
    id: str
    type: str
    content: str
    timestamp: str
    raw: dict[str, Any]


@dataclass(slots=True)
class GroupAtMessage(Event):
    group_openid: str
    author: Author
    member: Member


@dataclass(slots=True)
class C2CMessage(Event):
    author: Author


def parse_event(payload: dict[str, Any]) -> Event | None:
    event_type = payload.get("t")
    data = payload.get("d")
    if not isinstance(event_type, str) or not isinstance(data, dict):
        return None
    if event_type in (GROUP_AT_MESSAGE_CREATE, GROUP_MESSAGE_CREATE):
        return _parse_group_at(data)
    if event_type == C2C_MESSAGE_CREATE:
        return _parse_c2c(data)
    return None


def _parse_group_at(data: dict[str, Any]) -> GroupAtMessage:
    return GroupAtMessage(
        id=str(data.get("id", "")),
        type=GROUP_AT_MESSAGE_CREATE,
        content=str(data.get("content", "")),
        timestamp=str(data.get("timestamp", "")),
        raw=data,
        group_openid=str(data.get("group_openid", "")),
        author=Author.from_raw(data.get("author") or {}),
        member=Member.from_raw(data.get("member") or {}),
    )


def _parse_c2c(data: dict[str, Any]) -> C2CMessage:
    return C2CMessage(
        id=str(data.get("id", "")),
        type=C2C_MESSAGE_CREATE,
        content=str(data.get("content", "")),
        timestamp=str(data.get("timestamp", "")),
        raw=data,
        author=Author.from_raw(data.get("author") or {}),
    )