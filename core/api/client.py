import asyncio
import time
from typing import Any

import aiohttp
from loguru import logger

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
PRODUCTION_BASE = "https://api.sgroup.qq.com"
SANDBOX_BASE = "https://sandbox.api.sgroup.qq.com"
TOKEN_SAFETY_SECONDS = 60.0


class ApiClient:
    def __init__(self, app_id: str, app_secret: str, sandbox: bool = False) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = SANDBOX_BASE if sandbox else PRODUCTION_BASE
        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def token(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._expires_at:
                return self._token
            session = await self.session()
            async with session.post(
                TOKEN_URL,
                json={"appId": self._app_id, "clientSecret": self._app_secret},
            ) as resp:
                data = await resp.json(content_type=None)
            access_token = data.get("access_token")
            if not access_token:
                raise RuntimeError(f"获取 AccessToken 失败: {data}")
            self._token = str(access_token)
            self._expires_at = (
                time.monotonic() + _expires_in(data) - TOKEN_SAFETY_SECONDS
            )
            logger.info("AccessToken 已刷新")
            return self._token

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        session = await self.session()
        headers = {
            "Authorization": f"QQBot {await self.token()}",
            "Content-Type": "application/json",
        }
        async with session.request(
            method, f"{self._base_url}{path}", headers=headers, **kwargs
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                err_code = data.get("err_code") if isinstance(data, dict) else None
                message = data.get("message") if isinstance(data, dict) else data
                logger.error(
                    "API 请求失败 status={} path={} err_code={} message={}",
                    resp.status,
                    path,
                    err_code,
                    message,
                )
        return data if isinstance(data, dict) else {"data": data}

    async def post_group_message(
        self, group_openid: str, content: str, msg_id: str | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"content": content, "msg_type": 0}
        if msg_id:
            payload["msg_id"] = msg_id
        return await self.request(
            "POST", f"/v2/groups/{group_openid}/messages", json=payload
        )

    async def post_c2c_message(
        self, openid: str, content: str, msg_id: str | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"content": content, "msg_type": 0}
        if msg_id:
            payload["msg_id"] = msg_id
        return await self.request("POST", f"/v2/users/{openid}/messages", json=payload)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None


def _expires_in(data: dict[str, Any]) -> float:
    try:
        return float(data.get("expires_in", 0))
    except (TypeError, ValueError):
        return 0.0