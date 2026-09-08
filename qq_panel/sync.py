"""把配置里的面板 / 菜单同步到 QQ 平台。

同步是幂等的：本地记住 ``key -> panel_id`` 的映射，已存在的面板走「修改」，
不存在的才走「创建」，所以反复执行不会把面板越建越多。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .api import ERR_PANEL_NOT_FOUND, QQBotOpenAPI, QQOpenAPIError
from .models import MenuSpec, PanelSpec

STATE_KEY = "panel_ids"
"""KV 存储 / 状态文件里存放 key -> panel_id 映射的键名。"""


class PanelStateStore:
    """``key -> panel_id`` 映射的持久化。

    优先用 AstrBot 的插件 KV 存储（``Star.put_kv_data`` / ``get_kv_data``），
    在旧版本或 KV 不可用时回退到插件数据目录下的 JSON 文件。
    """

    def __init__(
        self,
        *,
        getter: Callable[[str, Any], Awaitable[Any]] | None = None,
        putter: Callable[[str, Any], Awaitable[None]] | None = None,
        fallback_path: Path | None = None,
    ) -> None:
        self._getter = getter
        self._putter = putter
        self._fallback_path = fallback_path
        self._cache: dict[str, str] | None = None

    async def load(self) -> dict[str, str]:
        """读取映射，结果会缓存在内存里。"""
        if self._cache is not None:
            return self._cache

        data: Any = None
        if self._getter is not None:
            try:
                data = await self._getter(STATE_KEY, None)
            except Exception:
                data = None
        if not isinstance(data, dict) and self._fallback_path is not None:
            data = self._read_file()

        self._cache = {
            str(k): str(v) for k, v in (data or {}).items() if k and v
        }
        return self._cache

    async def save(self) -> None:
        """写回映射。KV 写失败时落到 JSON 文件。"""
        payload = dict(self._cache or {})
        if self._putter is not None:
            try:
                await self._putter(STATE_KEY, payload)
                return
            except Exception:
                pass
        self._write_file(payload)

    async def get(self, key: str) -> str | None:
        """取某个 key 对应的 panel_id。"""
        return (await self.load()).get(key)

    async def set(self, key: str, panel_id: str) -> None:
        """记录某个 key 对应的 panel_id 并立即持久化。"""
        cache = await self.load()
        cache[key] = panel_id
        await self.save()

    async def forget(self, key: str) -> None:
        """删除某个 key 的映射（面板已被删除或已失效时）。"""
        cache = await self.load()
        if cache.pop(key, None) is not None:
            await self.save()

    async def all(self) -> dict[str, str]:
        """返回映射的副本。"""
        return dict(await self.load())

    def _read_file(self) -> dict[str, Any]:
        if self._fallback_path is None or not self._fallback_path.exists():
            return {}
        try:
            data = json.loads(self._fallback_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data.get(STATE_KEY, {}) if isinstance(data, dict) else {}

    def _write_file(self, payload: dict[str, str]) -> None:
        if self._fallback_path is None:
            return
        try:
            self._fallback_path.parent.mkdir(parents=True, exist_ok=True)
            self._fallback_path.write_text(
                json.dumps({STATE_KEY: payload}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass


@dataclass(slots=True)
class SyncReport:
    """一次同步的结果汇总。"""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    target_changed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """是否没有失败项。"""
        return not self.failed

    @property
    def changed(self) -> bool:
        """是否对平台做了实际写操作。"""
        return bool(self.created or self.updated or self.target_changed or self.deleted)

    def render(self) -> str:
        """渲染成可以直接回给管理员的一段文本。"""
        sections = [
            ("新建", self.created),
            ("更新", self.updated),
            ("关联对象变更", self.target_changed),
            ("删除", self.deleted),
            ("跳过", self.skipped),
            ("待清理", self.orphaned),
            ("失败", self.failed),
        ]
        lines = [
            f"{label}（{len(entries)}）：\n  " + "\n  ".join(entries)
            for label, entries in sections
            if entries
        ]
        if not lines:
            return "没有需要同步的面板。"
        return "\n".join(lines)


async def sync_panels(
    api: QQBotOpenAPI,
    specs: Sequence[PanelSpec],
    state: PanelStateStore,
    *,
    only_key: str | None = None,
    delete_removed: bool = False,
) -> SyncReport:
    """把面板配置同步到平台。

    Args:
        api: OpenAPI 客户端。
        specs: 解析后的面板配置（可含被禁用项）。
        state: ``key -> panel_id`` 映射存储。
        only_key: 只同步指定 key；为 None 时同步全部。
        delete_removed: 对配置中已删除 / 已禁用但线上仍存在的面板，
            True 表示直接删除，False 只在报告里列出。

    Returns:
        同步结果汇总。单个面板失败不会中断其余面板。
    """
    report = SyncReport()
    known = await state.all()
    config_keys = {s.key for s in specs}

    targets = [s for s in specs if only_key is None or s.key == only_key]
    if only_key is not None and not targets:
        report.failed.append(f"{only_key}: 配置里没有这个 key")
        return report

    for spec in targets:
        if not spec.enabled:
            report.skipped.append(f"{spec.key}: 配置里标记为 enabled=false")
            continue
        try:
            await _sync_one_panel(api, spec, state, report)
        except QQOpenAPIError as e:
            report.failed.append(f"{spec.key}: {e}")

    # 配置里已经不存在（或整体同步时被禁用）的 key，处理线上残留。
    if only_key is None:
        disabled_keys = {s.key for s in specs if not s.enabled}
        for key, panel_id in known.items():
            if key in config_keys and key not in disabled_keys:
                continue
            if not delete_removed:
                report.orphaned.append(
                    f"{key}: 线上面板 {panel_id} 仍存在，可用 /qqpanel delete {panel_id} 清理"
                )
                continue
            try:
                await api.delete_panel(panel_id)
                await state.forget(key)
                report.deleted.append(f"{key}: 已删除线上面板 {panel_id}")
            except QQOpenAPIError as e:
                if e.err_code == ERR_PANEL_NOT_FOUND:
                    await state.forget(key)
                    report.deleted.append(f"{key}: 线上面板已不存在，已清理本地记录")
                else:
                    report.failed.append(f"{key}: 删除面板 {panel_id} 失败：{e}")

    return report


async def _sync_one_panel(
    api: QQBotOpenAPI,
    spec: PanelSpec,
    state: PanelStateStore,
    report: SyncReport,
) -> None:
    """同步单个面板：已存在则改，不存在则建，再对齐关联对象。"""
    panel_id = await state.get(spec.key)
    detail: dict[str, Any] | None = None

    if panel_id:
        try:
            detail = await api.get_panel(panel_id)
        except QQOpenAPIError as e:
            if e.err_code != ERR_PANEL_NOT_FOUND:
                raise
            # 面板被人在后台删了，清掉映射走重建。
            await state.forget(spec.key)
            panel_id = None

    if not panel_id:
        panel_id = await api.create_panel(
            scope=spec.scope,
            panel=spec.panel_payload(),
            target_type=spec.target_type,
            user_openids=spec.user_openids if spec.scope == "c2c" else None,
            group_openids=spec.group_openids if spec.scope == "group" else None,
        )
        await state.set(spec.key, panel_id)
        report.created.append(
            f"{spec.key}: {panel_id}（{spec.scope}/{spec.target_type}，"
            f"{len(spec.items)} 个元素）"
        )
        # 创建时一次最多带 20 个 openid，剩下的靠下面的差量补齐。
    else:
        version = await api.update_panel(panel_id, spec.panel_payload())
        suffix = f"，version={version}" if version is not None else ""
        report.updated.append(
            f"{spec.key}: {panel_id}（{len(spec.items)} 个元素{suffix}）"
        )

    if spec.target_type != "specific":
        return

    if detail is None:
        detail = await api.get_panel(panel_id)
    field_name = "user_openids" if spec.scope == "c2c" else "group_openids"
    current = {str(i) for i in (detail.get(field_name) or [])}
    desired = set(spec.desired_openids)

    to_add = sorted(desired - current)
    to_del = sorted(current - desired)
    if not to_add and not to_del:
        return

    kwargs_key = field_name
    if to_add:
        await api.update_panel_target(panel_id, "add", **{kwargs_key: to_add})
    if to_del:
        await api.update_panel_target(panel_id, "del", **{kwargs_key: to_del})
    report.target_changed.append(
        f"{spec.key}: {panel_id} 新增 {len(to_add)} 个、移除 {len(to_del)} 个关联对象"
    )


async def sync_menu(api: QQBotOpenAPI, spec: MenuSpec) -> int | None:
    """把自定义菜单配置推送到平台。

    Args:
        api: OpenAPI 客户端。
        spec: 解析后的菜单配置。

    Returns:
        修改后的菜单版本号（平台未返回时为 None）。
    """
    return await api.set_menu(spec.menu_payload())
