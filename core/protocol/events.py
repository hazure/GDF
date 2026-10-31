from dataclasses import dataclass, field
from typing import Any

from core.protocol.models import Author, Member

GROUP_AT_MESSAGE_CREATE = "GROUP_AT_MESSAGE_CREATE"
GROUP_MESSAGE_CREATE = "GROUP_MESSAGE_CREATE"
C2C_MESSAGE_CREATE = "C2C_MESSAGE_CREATE"


@dataclass(slots=True)
class Event:
    id: str = ""
    type: str = ""
    content: str = ""
    timestamp: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GroupAtMessage(Event):
    group_openid: str = ""
    author: Author = field(default_factory=Author)
    member: Member = field(default_factory=Member)


@dataclass(slots=True)
class C2CMessage(Event):
    author: Author = field(default_factory=Author)


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


def _images_of(data: dict[str, Any]) -> list[str]:
    print(
        f"=== 触发 _images_of, attachments={data.get('attachments')}",
        flush=True,
    )
    images: list[str] = []
    for item in data.get("attachments") or []:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not url:
            continue
        images.append(str(url))
    print(f"=== 提取到 {len(images)} 张图片", flush=True)
    return images


def _parse_group_at(data: dict[str, Any]) -> GroupAtMessage:
    return GroupAtMessage(
        id=str(data.get("id", "")),
        type=GROUP_AT_MESSAGE_CREATE,
        content=str(data.get("content", "")),
        timestamp=str(data.get("timestamp", "")),
        raw=data,
        images=_images_of(data),
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
        images=_images_of(data),
        author=Author.from_raw(data.get("author") or {}),
    )