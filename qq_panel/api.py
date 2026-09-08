"""QQ 机器人开放平台 OpenAPI 客户端。

只封装本插件需要的接口：

- ``/app/getAppAccessToken``  获取访问凭证
- ``/v2/panels``             指令面板 列表 / 创建
- ``/v2/panels/{id}``        指令面板 详情 / 修改 / 删除
- ``/v2/panels/{id}/target`` 指令面板 关联对象增删
- ``/v2/menu``               单聊自定义菜单 查询 / 修改

文档：https://bot.q.qq.com/wiki/develop/api-v2/server-inter/menu-panel/
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable, Sequence
from typing import Any

import aiohttp

DEFAULT_BASE_URL = "https://api.bot.qq.com"

# 接口频率限制（QPM）-> 相邻两次请求的最小间隔（秒）。
# 文档给出的限制：panels GET 30 QPM、panels 写操作 10 QPM、
# panels target PUT 60 QPM、menu GET 30 QPM、menu PUT 5 QPM。
_QPM_TO_INTERVAL = {
    5: 12.0,
    10: 6.0,
    30: 2.0,
    60: 1.0,
}

# 平台错误码 -> 排查建议。取自各接口文档的「错误码」章节。
ERROR_HINTS: dict[int, str] = {
    100001: "请求过于频繁，请降低调用频率后重试",
    100007: "AppID 无效，或机器人状态不正常（被封禁 / 已删除）",
    100016: "AppID 或 AppSecret 不正确，请检查与开放平台管理端是否一致",
    10004: "AppID 对应的机器人不存在",
    11253: "机器人未获得调用该接口的权限，需要在开放平台申请",
    11254: "该接口已被封禁",
    11265: "机器人已被封禁",
    40030001: "参数错误，检查请求参数是否正确",
    40030006: "指令面板不存在，确认 panel_id 是否正确",
    40030008: "URL 格式错误，链接必须以 https:// 开头",
    40030009: "指令面板操作进行中，存在并发冲突，请稍后重试",
    40030011: "生效场景不合法，scope 仅支持 c2c/group/channel/dm",
    40030012: "生效范围不合法，target_type 仅支持 all/specific；channel/dm 仅支持 all",
    40030013: "超出数量限制，请减少请求数量",
    40030014: "菜单类型不合法，menu.type 仅支持 switch/send_message/link/menu",
    40030015: "面板元素类型不合法，panel_item.type 仅支持 command/link",
    40030016: "必填字段缺失，检查必填字段是否全部传入",
    40030017: "操作类型不合法，op 仅支持 add/del",
    40030018: "当前场景不支持此操作，检查 scope 是否支持",
    40030020: "内容存在安全风险，请检查菜单 / 面板内容是否包含敏感信息",
    40030021: "全局面板不支持指定关联对象，请把 target_type 改为 specific",
}

# 「面板操作进行中」，重试通常能解决。
ERR_OPERATION_IN_PROGRESS = 40030009
# 「指令面板不存在」，同步逻辑用它判断需要重建。
ERR_PANEL_NOT_FOUND = 40030006

# 关联对象接口一次最多 20 个 openid。
TARGET_BATCH_SIZE = 20


class QQOpenAPIError(Exception):
    """OpenAPI 调用失败。

    平台约定以响应体中的 ``err_code`` 判定失败，``message`` 可能随时调整，
    所以这里把 ``err_code`` 单独存下来供调用方分支处理。
    """

    def __init__(
        self,
        message: str,
        *,
        err_code: int | None = None,
        http_status: int | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_message = message
        self.err_code = err_code
        self.http_status = http_status
        self.trace_id = trace_id

    def __str__(self) -> str:
        parts: list[str] = []
        if self.err_code is not None:
            parts.append(f"err_code={self.err_code}")
        if self.http_status is not None:
            parts.append(f"HTTP {self.http_status}")
        prefix = f"[{' '.join(parts)}] " if parts else ""
        text = f"{prefix}{self.raw_message}"
        hint = ERROR_HINTS.get(self.err_code) if self.err_code is not None else None
        if hint:
            text += f"\n排查建议：{hint}"
        if self.trace_id:
            text += f"\ntrace_id: {self.trace_id}"
        return text


class QQBotOpenAPI:
    """QQ 机器人 OpenAPI 客户端。

    自行管理 access_token（缓存 + 提前刷新）与按接口的最小请求间隔。
    使用完毕后需要调用 :meth:`close` 释放底层连接。
    """

    def __init__(
        self,
        appid: str,
        secret: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 20.0,
        max_retries: int = 3,
    ) -> None:
        self.appid = appid
        self.secret = secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        self._token_expire_at: float = 0.0
        self._token_lock = asyncio.Lock()
        # (method, 限频分组) -> 上次请求的时间戳
        self._last_call_at: dict[str, float] = {}
        self._throttle_lock = asyncio.Lock()

    # ------------------------------------------------------------------ 基础设施

    @property
    def token_ready(self) -> bool:
        """是否已持有一个未过期的 access_token。"""
        return bool(self._token) and time.time() < self._token_expire_at

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                trust_env=True,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            )
        return self._session

    async def close(self) -> None:
        """关闭底层 HTTP 会话。"""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _get_token(self, *, force: bool = False) -> str:
        """获取 access_token，命中缓存时直接返回。

        Args:
            force: 为 True 时忽略缓存强制重新获取（如遇到 401）。

        Returns:
            可用于 ``Authorization: QQBot <token>`` 的凭证。
        """
        async with self._token_lock:
            if not force and self.token_ready:
                return self._token  # type: ignore[return-value]

            session = await self._get_session()
            url = f"{self.base_url}/app/getAppAccessToken"
            payload = {"appId": self.appid, "clientSecret": self.secret}
            try:
                async with session.post(url, json=payload) as resp:
                    data = await self._read_json(resp)
            except aiohttp.ClientError as e:
                raise QQOpenAPIError(f"获取 access_token 失败：{e}") from e

            # 该接口失败时同样返回 code/message（如 100016 invalid appid or secret）。
            err_code = _pick_err_code(data)
            token = data.get("access_token")
            if err_code or not token:
                raise QQOpenAPIError(
                    str(data.get("message") or "获取 access_token 失败"),
                    err_code=err_code,
                    trace_id=data.get("trace_id"),
                )

            # expires_in 文档里是数字，实际返回过字符串，两种都兼容。
            try:
                expires_in = int(data.get("expires_in") or 7200)
            except (TypeError, ValueError):
                expires_in = 7200
            self._token = str(token)
            # 提前 120s 过期，避开临界点。
            self._token_expire_at = time.time() + max(expires_in - 120, 60)
            return self._token

    async def _throttle(self, bucket: str, qpm: int) -> None:
        """按接口频率限制等待到允许发起下一次请求。"""
        interval = _QPM_TO_INTERVAL.get(qpm, 60.0 / max(qpm, 1))
        async with self._throttle_lock:
            now = time.monotonic()
            last = self._last_call_at.get(bucket)
            if last is not None:
                wait = interval - (now - last)
                if wait > 0:
                    await asyncio.sleep(wait)
                    now = time.monotonic()
            self._last_call_at[bucket] = now

    @staticmethod
    async def _read_json(resp: aiohttp.ClientResponse) -> dict[str, Any]:
        """读取响应体，把 204 / 空 body / 非 JSON 归一化成字典。"""
        raw = await resp.read()
        if not raw:
            return {}
        try:
            data = await resp.json(content_type=None)
        except Exception:
            return {"message": raw.decode("utf-8", "replace")[:500]}
        if isinstance(data, dict):
            return data
        return {"data": data}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        qpm: int,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        bucket: str | None = None,
    ) -> dict[str, Any]:
        """发起一次带鉴权的 OpenAPI 请求。

        Args:
            method: HTTP 方法。
            path: 以 ``/`` 开头的接口路径。
            qpm: 该接口的频率限制，用于计算最小请求间隔。
            params: URL 查询参数，值为 None 的项会被丢弃。
            json_body: JSON 请求体。
            bucket: 限频分组名，默认取 ``method + path 前缀``。

        Returns:
            解析后的响应体；无包体的接口返回空字典。

        Raises:
            QQOpenAPIError: 网络错误，或响应中带有非 0 的 ``err_code``。
        """
        url = f"{self.base_url}{path}"
        clean_params = (
            {k: v for k, v in params.items() if v is not None and v != ""}
            if params
            else None
        )
        bucket = bucket or f"{method} {path.rsplit('/', 1)[0]}"

        last_error: QQOpenAPIError | None = None
        for attempt in range(self.max_retries):
            await self._throttle(bucket, qpm)
            token = await self._get_token()
            session = await self._get_session()
            headers = {
                "Authorization": f"QQBot {token}",
                "Content-Type": "application/json; charset=utf-8",
            }
            try:
                async with session.request(
                    method,
                    url,
                    params=clean_params,
                    json=json_body,
                    headers=headers,
                ) as resp:
                    data = await self._read_json(resp)
                    status = resp.status
                    trace_id = data.get("trace_id") or resp.headers.get(
                        "X-Tps-trace-ID"
                    )
            except aiohttp.ClientError as e:
                last_error = QQOpenAPIError(f"{method} {path} 请求失败：{e}")
                if attempt + 1 < self.max_retries:
                    await asyncio.sleep(2**attempt)
                    continue
                raise last_error from e

            err_code = _pick_err_code(data)

            # 凭证失效：清掉缓存重取一次。
            if status == 401 and attempt + 1 < self.max_retries:
                await self._get_token(force=True)
                continue

            # 限频 / 面板操作进行中：退避重试。
            retryable = status == 429 or err_code == ERR_OPERATION_IN_PROGRESS
            if retryable and attempt + 1 < self.max_retries:
                await asyncio.sleep(2**attempt + 1)
                continue

            if err_code or status >= 400:
                raise QQOpenAPIError(
                    str(data.get("message") or f"{method} {path} 调用失败"),
                    err_code=err_code,
                    http_status=status,
                    trace_id=trace_id,
                )
            return data

        # 循环只会因为 continue 走到这里，兜底抛出最后一次错误。
        raise last_error or QQOpenAPIError(f"{method} {path} 重试 {self.max_retries} 次后仍失败")

    # ------------------------------------------------------------------ 指令面板

    async def list_panels(
        self,
        scope: str,
        *,
        cursor: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        """分页查询指定场景下已生效的指令面板列表。

        Args:
            scope: 生效场景，c2c / group / channel / dm。
            cursor: 分页游标，首次请求传空串。
            limit: 每页条数，默认 20，最大 50。

        Returns:
            含 ``records`` / ``next_cursor`` / ``is_end`` 的响应体。
        """
        return await self._request(
            "GET",
            "/v2/panels",
            qpm=30,
            params={"scope": scope, "cursor": cursor, "limit": min(max(limit, 1), 50)},
            bucket="GET /v2/panels",
        )

    async def iter_all_panels(self, scope: str) -> list[dict[str, Any]]:
        """翻完所有分页，返回某个场景下的全部面板记录。"""
        records: list[dict[str, Any]] = []
        cursor = ""
        # 面板总数上限 20，最多翻几页；给个硬上限防止服务端游标异常导致死循环。
        for _ in range(20):
            data = await self.list_panels(scope, cursor=cursor, limit=50)
            records.extend(data.get("records") or [])
            cursor = data.get("next_cursor") or ""
            if data.get("is_end") or not cursor:
                break
        return records

    async def create_panel(
        self,
        *,
        scope: str,
        panel: dict[str, Any],
        target_type: str = "all",
        user_openids: Sequence[str] | None = None,
        group_openids: Sequence[str] | None = None,
    ) -> str:
        """创建指令面板。

        Args:
            scope: 生效场景。
            panel: 面板配置内容（``items`` / ``remark``）。
            target_type: all 或 specific。
            user_openids: 仅 c2c + specific 有效，一次最多 20 个。
            group_openids: 仅 group + specific 有效，一次最多 20 个。

        Returns:
            新建面板的 panel_id。
        """
        body: dict[str, Any] = {
            "scope": scope,
            "target_type": target_type,
            "panel": panel,
        }
        if target_type == "specific":
            if user_openids:
                body["user_openids"] = list(user_openids)[:TARGET_BATCH_SIZE]
            if group_openids:
                body["group_openids"] = list(group_openids)[:TARGET_BATCH_SIZE]
        data = await self._request("POST", "/v2/panels", qpm=10, json_body=body)
        panel_id = data.get("panel_id")
        if not panel_id:
            raise QQOpenAPIError("创建指令面板成功但未返回 panel_id", trace_id=data.get("trace_id"))
        return str(panel_id)

    async def get_panel(self, panel_id: str) -> dict[str, Any]:
        """查询指令面板详情，含关联的 openid 列表。"""
        return await self._request(
            "GET",
            f"/v2/panels/{panel_id}",
            qpm=30,
            bucket="GET /v2/panels/{id}",
        )

    async def update_panel(self, panel_id: str, panel: dict[str, Any]) -> int | None:
        """覆盖修改面板内容，不影响已关联的用户 / 群列表。

        Returns:
            修改后的面板版本号（平台未返回时为 None）。
        """
        data = await self._request(
            "PUT",
            f"/v2/panels/{panel_id}",
            qpm=10,
            json_body={"panel": panel},
            bucket="PUT /v2/panels/{id}",
        )
        return _as_int(data.get("version"))

    async def delete_panel(self, panel_id: str) -> None:
        """删除指令面板。"""
        await self._request(
            "DELETE",
            f"/v2/panels/{panel_id}",
            qpm=10,
            bucket="DELETE /v2/panels/{id}",
        )

    async def update_panel_target(
        self,
        panel_id: str,
        op: str,
        *,
        user_openids: Sequence[str] | None = None,
        group_openids: Sequence[str] | None = None,
    ) -> None:
        """增删面板关联对象。

        一次最多 20 个 openid，超出的部分由本方法自动分批。

        Args:
            panel_id: 面板 ID。
            op: add 或 del。
            user_openids: 用户 openid，仅 c2c 场景有效。
            group_openids: 群 openid，仅 group 场景有效。
        """
        if op not in ("add", "del"):
            raise QQOpenAPIError(f"op 仅支持 add/del，收到 {op!r}", err_code=40030017)

        for batch in _chunks(user_openids or (), TARGET_BATCH_SIZE):
            await self._request(
                "PUT",
                f"/v2/panels/{panel_id}/target",
                qpm=60,
                json_body={"op": op, "user_openids": batch},
                bucket="PUT /v2/panels/{id}/target",
            )
        for batch in _chunks(group_openids or (), TARGET_BATCH_SIZE):
            await self._request(
                "PUT",
                f"/v2/panels/{panel_id}/target",
                qpm=60,
                json_body={"op": op, "group_openids": batch},
                bucket="PUT /v2/panels/{id}/target",
            )

    # ------------------------------------------------------------------ 自定义菜单

    async def get_menu(self) -> dict[str, Any]:
        """查询当前生效的单聊自定义菜单。"""
        return await self._request("GET", "/v2/menu", qpm=30)

    async def set_menu(self, menu: dict[str, Any]) -> int | None:
        """覆盖修改单聊自定义菜单。

        Args:
            menu: 菜单配置（``{"items": [...]}）``。

        Returns:
            修改后的菜单版本号（平台未返回时为 None）。
        """
        data = await self._request("PUT", "/v2/menu", qpm=5, json_body={"menu": menu})
        return _as_int(data.get("version"))


def _as_int(value: Any) -> int | None:
    """尽力把响应里的数值字段转成 int，失败返回 None。"""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _pick_err_code(data: dict[str, Any]) -> int | None:
    """从响应体中取出非 0 的错误码。

    平台在不同接口上分别用过 ``err_code``、``code``、``retcode``，都兼容一下。
    """
    for field in ("err_code", "code", "retcode"):
        value = data.get(field)
        if value in (None, 0, "0"):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _chunks(items: Iterable[str], size: int) -> list[list[str]]:
    """把序列切成不超过 size 的批次，空输入返回空列表。"""
    buf = [i for i in items if i]
    return [buf[i : i + size] for i in range(0, len(buf), size)]
