"""QQ 官方机器人「指令面板 / 自定义菜单」管理插件。

把指令面板和单聊自定义菜单的配置放进 AstrBot 插件配置里，用管理员指令一键
推送到 QQ 开放平台，避免每次改动都要去开放平台后台手点。

QQ 官方文档：https://bot.q.qq.com/wiki/develop/api-v2/server-inter/menu-panel/
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.star.filter.command import GreedyStr

from .qq_panel import (
    ConfigError,
    MenuSpec,
    PanelSpec,
    PanelStateStore,
    QQBotOpenAPI,
    QQOpenAPIError,
    load_menu,
    load_panels,
)
from .qq_panel.models import SCOPES, SCOPE_LABELS

QQ_PLATFORM_NAMES = ("qq_official", "qq_official_webhook")
"""本插件能读取凭证的平台适配器（见 AstrBot core/platform/sources/）。"""


class QQPanelPlugin(Star):
    """管理 QQ 官方机器人的指令面板与单聊自定义菜单。

    用 /qqpanel help 查看全部子指令。
    """

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self._api: QQBotOpenAPI | None = None
        self._api_source: str = ""
        self._state: PanelStateStore | None = None
        self._last_sync_at: float | None = None
        self._bg_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ 生命周期

    async def terminate(self) -> None:
        """插件停用 / 重载时释放 HTTP 连接并取消后台任务。"""
        if self._bg_task is not None and not self._bg_task.done():
            self._bg_task.cancel()
        if self._api is not None:
            await self._api.close()
            self._api = None

    @filter.on_astrbot_loaded()
    async def _auto_sync(self) -> None:
        """AstrBot 启动完成后，按配置自动推送一次面板 / 菜单。"""
        if not self.config.get("auto_sync_on_start", False):
            return
        self._bg_task = asyncio.create_task(self._run_auto_sync())

    async def _run_auto_sync(self) -> None:
        """后台执行启动同步。失败只记日志，不影响 AstrBot 启动。"""
        try:
            api = self._get_api()
            specs = self._load_panels()
            report = await self._sync_panels(api, specs)
            if report.changed or report.orphaned or not report.ok:
                logger.info("启动自动同步指令面板：\n%s", report.render())
            else:
                logger.info("启动自动同步指令面板：无变更。")

            if self.config.get("auto_sync_menu", False):
                menu = self._load_menu()
                if menu.items:
                    from .qq_panel import sync_menu

                    version = await sync_menu(api, menu)
                    logger.info("启动自动同步自定义菜单完成，version=%s", version)
        except asyncio.CancelledError:
            raise
        except (ConfigError, QQOpenAPIError, RuntimeError) as e:
            logger.error("启动自动同步失败：%s", e)
        except Exception:
            logger.exception("启动自动同步时出现未预期的错误")

    # ------------------------------------------------------------------ 内部工具

    def _get_api(self) -> QQBotOpenAPI:
        """构造（并缓存）OpenAPI 客户端。

        凭证优先取插件配置里的覆盖值，否则从 QQ 官方平台适配器实例上读。

        Returns:
            可用的 OpenAPI 客户端。

        Raises:
            RuntimeError: 找不到 QQ 官方适配器，或凭证缺失 / 存在多个适配器
                但未指定 platform_id。
        """
        if self._api is not None:
            return self._api

        creds = self.config.get("credentials") or {}
        appid = str(creds.get("appid") or "").strip()
        secret = str(creds.get("secret") or "").strip()
        source = "插件配置"

        if not (appid and secret):
            adapter = self._find_adapter()
            appid = str(getattr(adapter, "appid", "") or "").strip()
            secret = str(getattr(adapter, "secret", "") or "").strip()
            source = f"平台适配器 {adapter.meta().id}（{adapter.meta().name}）"

        if not (appid and secret):
            raise RuntimeError(
                "没有拿到 AppID / AppSecret。请检查 QQ 官方机器人适配器的配置，"
                "或在本插件配置的 credentials 里手动填写。"
            )

        self._api = QQBotOpenAPI(appid, secret)
        self._api_source = source
        return self._api

    def _find_adapter(self) -> Any:
        """定位 QQ 官方机器人平台适配器实例。

        Returns:
            适配器实例。

        Raises:
            RuntimeError: 没找到，或找到多个但配置里没指定 platform_id。
        """
        platform_id = str(self.config.get("platform_id") or "").strip()
        if platform_id:
            adapter = self.context.get_platform_inst(platform_id)
            if adapter is None:
                raise RuntimeError(f"找不到 ID 为 {platform_id!r} 的平台适配器。")
            if adapter.meta().name not in QQ_PLATFORM_NAMES:
                raise RuntimeError(
                    f"平台 {platform_id!r} 的类型是 {adapter.meta().name}，"
                    f"不是 QQ 官方机器人适配器。"
                )
            return adapter

        candidates = [
            p
            for p in self.context.platform_manager.platform_insts
            if p.meta().name in QQ_PLATFORM_NAMES
        ]
        if not candidates:
            raise RuntimeError(
                "没有启用的 QQ 官方机器人适配器（qq_official / qq_official_webhook）。"
                "请先在 WebUI 里配置并启用，或在本插件配置里手动填写 credentials。"
            )
        if len(candidates) > 1:
            ids = "、".join(p.meta().id for p in candidates)
            raise RuntimeError(
                f"检测到多个 QQ 官方机器人适配器（{ids}），"
                f"请在本插件配置的 platform_id 里指定要管理哪一个。"
            )
        return candidates[0]

    def _get_state(self) -> PanelStateStore:
        """构造（并缓存）panel_id 映射存储。"""
        if self._state is None:
            fallback: Path | None = None
            try:
                fallback = StarTools.get_data_dir("astrbot_plugin_qq_panel") / "state.json"
            except Exception:
                fallback = None
            self._state = PanelStateStore(
                getter=self.get_kv_data,
                putter=self.put_kv_data,
                fallback_path=fallback,
            )
        return self._state

    async def _sync_panels(
        self,
        api: QQBotOpenAPI,
        specs: list[PanelSpec],
        *,
        only_key: str | None = None,
    ):
        """执行面板同步并记录同步时间。"""
        from .qq_panel import sync_panels

        report = await sync_panels(
            api,
            specs,
            self._get_state(),
            only_key=only_key,
            delete_removed=bool(self.config.get("delete_removed_panels", False)),
        )
        self._last_sync_at = time.time()
        return report

    def _load_panels(self) -> list[PanelSpec]:
        """解析面板配置（可视化条目 + 可选的高级 JSON）。"""
        return load_panels(
            self.config.get("panel_items"),
            user_openids=self.config.get("target_user_openids"),
            group_openids=self.config.get("target_group_openids"),
            advanced_json=self.config.get("advanced_panels_json"),
        )

    def _load_menu(self) -> MenuSpec:
        """解析菜单配置（可视化条目，或高级 JSON 覆盖）。"""
        return load_menu(
            self.config.get("menu_items"),
            advanced_json=self.config.get("advanced_menu_json"),
        )

    # ------------------------------------------------------------------ 指令

    @filter.command_group("qqpanel", alias={"指令面板"})
    def qqpanel(self):
        """QQ 官方机器人指令面板 / 自定义菜单管理。"""

    @filter.permission_type(filter.PermissionType.ADMIN)
    @qqpanel.command("status")
    async def status(self, event: AstrMessageEvent):
        """查看当前凭证来源、配置解析结果与已托管的面板。"""
        lines = ["QQ 指令面板插件状态", ""]

        try:
            api = self._get_api()
            lines.append(f"凭证来源：{self._api_source}")
            lines.append(f"AppID：{_mask(api.appid)}")
            lines.append(f"access_token：{'已缓存' if api.token_ready else '尚未获取'}")
        except RuntimeError as e:
            lines.append(f"凭证：不可用 —— {e}")

        try:
            specs = self._load_panels()
            enabled = [s for s in specs if s.enabled]
            if enabled:
                detail = "、".join(
                    f"{SCOPE_LABELS.get(s.scope, s.scope)} {len(s.items)} 个按钮"
                    for s in enabled
                )
                lines.append(f"面板配置：{len(enabled)} 个面板（{detail}）")
            else:
                lines.append("面板配置：还没有按钮")
        except ConfigError as e:
            lines.append(f"面板配置：有问题\n{e}")

        try:
            menu = self._load_menu()
            lines.append(f"菜单配置：{len(menu.items)} 个一级条目")
        except ConfigError as e:
            lines.append(f"菜单配置：有问题\n{e}")

        mapping = await self._get_state().all()
        if mapping:
            lines.append("")
            lines.append("已托管的面板：")
            lines.extend(f"  {key} → {pid}" for key, pid in mapping.items())
        else:
            lines.append("")
            lines.append("已托管的面板：（还没有，执行 /qqpanel sync 创建）")

        if self._last_sync_at:
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._last_sync_at))
            lines.append(f"上次同步：{when}")

        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @qqpanel.command("preview")
    async def preview(self, event: AstrMessageEvent):
        """只做本地校验，预览将要推送的面板与菜单（不调用平台接口）。"""
        lines: list[str] = []
        try:
            specs = self._load_panels()
        except ConfigError as e:
            yield event.plain_result(f"面板配置有问题：\n{e}")
            return
        try:
            menu = self._load_menu()
        except ConfigError as e:
            yield event.plain_result(f"菜单配置有问题：\n{e}")
            return

        lines.append(f"指令面板（{len(specs)} 个）")
        if specs:
            lines.extend(s.summary() for s in specs)
        else:
            lines.append("（未配置）")

        lines.append("")
        lines.append(f"单聊自定义菜单（{len(menu.items)} 项）")
        lines.append(menu.summary())
        lines.append("")
        lines.append("校验通过。执行 /qqpanel sync 推送面板，/qqpanel menu sync 推送菜单。")

        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @qqpanel.command("sync")
    async def sync(self, event: AstrMessageEvent, key: str = ""):
        """把面板配置推送到平台。可只同步指定 key：/qqpanel sync <key>"""
        try:
            specs = self._load_panels()
        except ConfigError as e:
            yield event.plain_result(f"面板配置有问题，已中止同步：\n{e}")
            return
        if not specs:
            yield event.plain_result("可视化面板配置里没有生成任何面板。请在 panel_items 里至少添加一条并选择聊天场景。")
            return

        try:
            api = self._get_api()
            report = await self._sync_panels(api, specs, only_key=key.strip() or None)
        except (RuntimeError, QQOpenAPIError) as e:
            yield event.plain_result(f"同步失败：{e}")
            return

        head = "同步完成。" if report.ok else "同步完成，但有失败项。"
        yield event.plain_result(f"{head}\n{report.render()}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @qqpanel.command("list")
    async def list_panels(self, event: AstrMessageEvent, scope: str = ""):
        """列出平台上已生效的面板。可指定场景：c2c / group / channel / dm"""
        scope = scope.strip()
        if scope and scope not in SCOPES:
            yield event.plain_result(f"scope 必须是 {'/'.join(SCOPES)} 之一。")
            return
        scopes = [scope] if scope else list(SCOPES)

        try:
            api = self._get_api()
        except RuntimeError as e:
            yield event.plain_result(str(e))
            return

        mapping = {pid: key for key, pid in (await self._get_state().all()).items()}
        lines: list[str] = []
        total = 0
        for one in scopes:
            try:
                records = await api.iter_all_panels(one)
            except QQOpenAPIError as e:
                lines.append(f"[{one}] 查询失败：{e}")
                continue
            if not records:
                lines.append(f"[{one}] 无面板")
                continue
            lines.append(f"[{one}] {len(records)} 个：")
            for record in records:
                total += 1
                lines.append(f"  {_format_panel_record(record, mapping)}")

        lines.append("")
        lines.append(f"共 {total} 个面板（平台上限 20 个）。")
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @qqpanel.command("show")
    async def show(self, event: AstrMessageEvent, panel_id: str = ""):
        """查看面板详情：/qqpanel show <panel_id>"""
        panel_id = panel_id.strip()
        if not panel_id:
            yield event.plain_result("用法：/qqpanel show <panel_id>（panel_id 可从 /qqpanel list 获取）")
            return
        try:
            api = self._get_api()
            detail = await api.get_panel(panel_id)
        except (RuntimeError, QQOpenAPIError) as e:
            yield event.plain_result(f"查询失败：{e}")
            return

        panel = detail.get("panel") or {}
        items = panel.get("items") or []
        lines = [
            f"panel_id：{detail.get('panel_id', panel_id)}",
            f"场景 / 范围：{detail.get('scope', '?')} / {detail.get('target_type', '?')}",
            f"版本：{detail.get('version', '?')}",
        ]
        if panel.get("remark"):
            lines.append(f"备注：{panel['remark']}")
        if detail.get("created_at"):
            lines.append(f"创建时间：{detail['created_at']}")
        if detail.get("updated_at"):
            lines.append(f"更新时间：{detail['updated_at']}")

        lines.append("")
        lines.append(f"面板元素（{len(items)}）：")
        for i, item in enumerate(items, 1):
            lines.append(f"  {i}. {_format_remote_item(item)}")

        for field_name, label in (("user_openids", "关联用户"), ("group_openids", "关联群")):
            ids = detail.get(field_name) or []
            if ids:
                lines.append("")
                lines.append(f"{label}（{len(ids)}）：")
                lines.extend(f"  {i}" for i in ids[:20])
                if len(ids) > 20:
                    lines.append(f"  …… 还有 {len(ids) - 20} 个")

        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @qqpanel.command("delete")
    async def delete(self, event: AstrMessageEvent, panel_id: str = ""):
        """删除平台上的面板：/qqpanel delete <panel_id>"""
        panel_id = panel_id.strip()
        if not panel_id:
            yield event.plain_result("用法：/qqpanel delete <panel_id>")
            return
        try:
            api = self._get_api()
            await api.delete_panel(panel_id)
        except (RuntimeError, QQOpenAPIError) as e:
            yield event.plain_result(f"删除失败：{e}")
            return

        state = self._get_state()
        for key, pid in (await state.all()).items():
            if pid == panel_id:
                await state.forget(key)
        yield event.plain_result(
            f"已删除面板 {panel_id}。\n"
            f"注意：如果 panel_items 或 advanced_panels_json 里仍会生成对应配置，下次 sync 会重新创建。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @qqpanel.command("target")
    async def target(self, event: AstrMessageEvent, args: GreedyStr):
        """增删面板关联对象：/qqpanel target <add|del> <key 或 panel_id> <openid...>"""
        parts = str(args).split()
        op = parts[0] if parts else ""
        ref = parts[1] if len(parts) > 1 else ""
        ids = parts[2:]
        if op not in ("add", "del") or not ref or not ids:
            yield event.plain_result(
                "用法：/qqpanel target <add|del> <key 或 panel_id> <openid...>\n"
                "c2c 场景传 user_openid，group 场景传 group_openid，多个用空格分隔。"
            )
            return

        try:
            api = self._get_api()
        except RuntimeError as e:
            yield event.plain_result(str(e))
            return

        panel_id = await self._get_state().get(ref) or ref
        try:
            detail = await api.get_panel(panel_id)
            scope = str(detail.get("scope") or "")
            if detail.get("target_type") != "specific":
                yield event.plain_result(
                    f"面板 {panel_id} 的 target_type 是 "
                    f"{detail.get('target_type')}，全局面板不支持指定关联对象。"
                )
                return
            key_name = "user_openids" if scope == "c2c" else "group_openids"
            await api.update_panel_target(panel_id, op, **{key_name: ids})
        except (RuntimeError, QQOpenAPIError) as e:
            yield event.plain_result(f"操作失败：{e}")
            return

        action = "添加" if op == "add" else "移除"
        yield event.plain_result(
            f"已为面板 {panel_id} {action} {len(ids)} 个关联对象。\n"
            f"提示：下次 sync 会以 target_user_openids / target_group_openids（或高级面板里的 openid 列表）为依据，记得同步更新配置。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @qqpanel.command("help")
    async def help_cmd(self, event: AstrMessageEvent):
        """查看本插件的全部指令。"""
        yield event.plain_result(
            "QQ 指令面板插件\n"
            "  /qqpanel status                  查看凭证、配置与已托管面板\n"
            "  /qqpanel preview                 本地校验并预览待推送内容\n"
            "  /qqpanel sync [key]              推送面板配置（可只推一个 key）\n"
            "  /qqpanel list [scope]            列出平台上的面板\n"
            "  /qqpanel show <panel_id>         查看面板详情\n"
            "  /qqpanel delete <panel_id>       删除平台上的面板\n"
            "  /qqpanel target <add|del> <key|panel_id> <openid...>\n"
            "                                   增删面板关联对象\n"
            "  /qqpanel menu show               查看线上自定义菜单\n"
            "  /qqpanel menu sync               推送自定义菜单\n"
            "  /qqpanel menu clear              清空自定义菜单\n"
            "\n面板 / 菜单内容在 WebUI 的插件配置里编辑（panel_items / menu_items）。高级 JSON 配置见 advanced_panels_json / advanced_menu_json。"
        )

    # ------------------------------------------------------------------ 自定义菜单

    @qqpanel.group("menu")
    def menu_group(self):
        """单聊自定义菜单管理。"""

    @filter.permission_type(filter.PermissionType.ADMIN)
    @menu_group.command("show")
    async def menu_show(self, event: AstrMessageEvent):
        """查看平台上当前生效的自定义菜单。"""
        try:
            api = self._get_api()
            data = await api.get_menu()
        except (RuntimeError, QQOpenAPIError) as e:
            yield event.plain_result(f"查询失败：{e}")
            return

        menu = data.get("menu") or {}
        items = menu.get("items") or []
        if not items:
            yield event.plain_result("平台上还没有设置自定义菜单。")
            return

        try:
            spec = MenuSpec.from_dict(menu)
            body = spec.summary()
        except ConfigError:
            # 平台返回了插件不认识的结构，退化成原始展示。
            body = "\n".join(f"[{i.get('type')}] {i.get('name')}" for i in items)

        yield event.plain_result(
            f"当前自定义菜单（version={data.get('version', '?')}）：\n{body}"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @menu_group.command("sync")
    async def menu_sync(self, event: AstrMessageEvent):
        """把 menu_items（或 advanced_menu_json）推送到平台（覆盖原有菜单）。"""
        try:
            spec = self._load_menu()
        except ConfigError as e:
            yield event.plain_result(f"菜单配置有问题，已中止：\n{e}")
            return
        if not spec.items:
            yield event.plain_result(
                "menu_items 里没有配置菜单项。如果想清空线上菜单，请用 /qqpanel menu clear。"
            )
            return

        from .qq_panel import sync_menu

        try:
            api = self._get_api()
            version = await sync_menu(api, spec)
        except (RuntimeError, QQOpenAPIError) as e:
            yield event.plain_result(f"推送失败：{e}")
            return

        yield event.plain_result(
            f"自定义菜单已推送（{len(spec.items)} 项，version={version}）。\n"
            f"菜单仅在单聊窗口底部展示，客户端可能需要几分钟或重进会话才能看到。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @menu_group.command("clear")
    async def menu_clear(self, event: AstrMessageEvent):
        """清空平台上的自定义菜单。"""
        try:
            api = self._get_api()
            version = await api.set_menu({"items": []})
        except (RuntimeError, QQOpenAPIError) as e:
            yield event.plain_result(f"清空失败：{e}")
            return
        yield event.plain_result(f"自定义菜单已清空（version={version}）。")


def _mask(value: str) -> str:
    """遮罩敏感字符串，只保留首尾各 3 位。"""
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}{'*' * (len(value) - 6)}{value[-3:]}"


def _format_panel_record(record: dict[str, Any], id_to_key: dict[str, str]) -> str:
    """把面板列表里的一条记录格式化成一行。"""
    panel_id = str(record.get("panel_id", "?"))
    panel = record.get("panel") or {}
    items = panel.get("items") or []
    bits = [panel_id, f"{record.get('target_type', '?')}", f"{len(items)} 个元素"]
    if record.get("version") is not None:
        bits.append(f"v{record['version']}")
    if panel_id in id_to_key:
        bits.append(f"← 本插件托管（key={id_to_key[panel_id]}）")
    if panel.get("remark"):
        bits.append(f"备注：{panel['remark']}")
    return " | ".join(bits)


def _format_remote_item(item: dict[str, Any]) -> str:
    """把平台返回的面板元素格式化成一行。"""
    bits = [f"[{item.get('type', '?')}] {item.get('name', '')}"]
    if item.get("desc"):
        bits.append(f"- {item['desc']}")
    if item.get("link"):
        bits.append(f"→ {item['link']}")
    if item.get("only_admin"):
        bits.append("(仅管理员)")
    return " ".join(bits)
