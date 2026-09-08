"""指令面板 / 自定义菜单的配置模型与校验。

平台侧对字段长度、类型、场景组合都有硬限制，违规会直接返回错误码。
这里在本地先把配置校验一遍，既能一次性报出所有问题，也省掉无谓的 API 往返。
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# ------------------------------------------------------------------ 平台限制

SCOPES = ("c2c", "group", "channel", "dm")
"""生效场景：单聊 / 群聊 / 文字子频道 / 频道私信。"""

TARGET_TYPES = ("all", "specific")
"""作用范围：全局配置 / 指定用户或群生效。"""

SCOPES_SUPPORTING_SPECIFIC = ("c2c", "group")
"""只有 c2c 和 group 支持 specific，channel / dm 只能 all。"""

PANEL_ITEM_TYPES = ("command", "link")
MENU_ITEM_TYPES = ("switch", "send_message", "link", "menu")
SUB_MENU_ITEM_TYPES = ("send_message", "link")

MAX_PANELS = 20
"""一个机器人最多创建 20 个指令面板。"""

MAX_PANEL_ITEMS = 20
"""一个指令面板里最多 20 个面板元素。"""

MAX_PANEL_ITEM_NAME_WIDTH = 14
MAX_PANEL_ITEM_DESC_WIDTH = 30
MAX_REMARK_LEN = 255

MAX_MENU_ITEMS = 10
"""自定义菜单一级菜单项最多 10 个。"""

MAX_MENU_ITEM_NAME_WIDTH = 10
MAX_SUB_MENU_ITEMS = 5
MAX_SUB_MENU_ITEM_NAME_WIDTH = 14

MAX_OPENIDS_PER_PANEL = 1000
"""面板详情最多返回 1000 条关联 openid。"""

# ------------------------------------------------------------------ 可视化配置

SCOPE_LABELS = {
    "c2c": "单聊",
    "group": "群聊",
    "channel": "文字子频道",
    "dm": "频道私信",
}
"""场景的中文名，用于面向用户的提示。"""

AUTO_KEY_PREFIX = "auto_"
"""由可视化配置自动生成的面板，其本地 key 统一用这个前缀。"""

PANEL_ENTRY_KINDS = ("command", "link")
"""``panel_items`` 支持的条目模板。"""

MENU_ENTRY_KINDS = ("command", "link", "folder", "switch")
"""``menu_items`` 支持的条目模板。"""

MENU_TYPE_LABELS = {
    "send_message": "指令",
    "link": "链接",
    "switch": "开关",
    "menu": "折叠菜单",
}
"""菜单项类型的中文名，用于面向用户的输出。"""


class ConfigError(Exception):
    """插件配置不合法。消息里会带上出错的位置，便于在 WebUI 里定位。"""


def text_width(text: str) -> int:
    """按平台的字符计数方式计算字符串宽度。

    文档对长度的描述是「最多 14 个字符，约 7 个中文汉字」，即一个全角字符
    算 2 个字符。这里用 East Asian Width 判定：W（宽）和 F（全角）算 2，
    其余算 1。

    Args:
        text: 待计算的字符串。

    Returns:
        字符串的字符宽度。
    """
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


# ------------------------------------------------------------------ 指令面板


@dataclass(slots=True)
class PanelItem:
    """面板元素：一条指令或一个跳转链接。"""

    type: str
    name: str
    desc: str = ""
    only_admin: bool = False
    link: str = ""

    @classmethod
    def from_dict(cls, raw: Any, where: str) -> PanelItem:
        """解析并校验单个面板元素。

        Args:
            raw: 配置里的元素对象。
            where: 出错时用于定位的路径描述。

        Returns:
            解析后的面板元素。

        Raises:
            ConfigError: 字段类型、取值或长度不合法。
        """
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: 面板元素必须是对象，收到 {type(raw).__name__}")

        item_type = str(raw.get("type") or "").strip()
        if item_type not in PANEL_ITEM_TYPES:
            raise ConfigError(
                f"{where}: type 必须是 {'/'.join(PANEL_ITEM_TYPES)}，收到 {item_type!r}"
            )

        name = str(raw.get("name") or "").strip()
        if not name:
            raise ConfigError(f"{where}: name 不能为空")
        if text_width(name) > MAX_PANEL_ITEM_NAME_WIDTH:
            raise ConfigError(
                f"{where}: name {name!r} 过长（{text_width(name)} > "
                f"{MAX_PANEL_ITEM_NAME_WIDTH} 字符，汉字算 2 个）"
            )

        desc = str(raw.get("desc") or "").strip()
        if text_width(desc) > MAX_PANEL_ITEM_DESC_WIDTH:
            raise ConfigError(
                f"{where}: desc {desc!r} 过长（{text_width(desc)} > "
                f"{MAX_PANEL_ITEM_DESC_WIDTH} 字符，汉字算 2 个）"
            )

        link = str(raw.get("link") or "").strip()
        if item_type == "link":
            if not link:
                raise ConfigError(f"{where}: type=link 必须提供 link")
            if not link.startswith("https://"):
                raise ConfigError(f"{where}: link 必须以 https:// 开头，收到 {link!r}")
        elif link:
            raise ConfigError(f"{where}: type=command 不应提供 link")

        return cls(
            type=item_type,
            name=name,
            desc=desc,
            only_admin=bool(raw.get("only_admin", False)),
            link=link,
        )

    def to_payload(self) -> dict[str, Any]:
        """转换成 API 请求体里的 PanelItem。"""
        payload: dict[str, Any] = {"type": self.type, "name": self.name}
        if self.desc:
            payload["desc"] = self.desc
        if self.only_admin:
            payload["only_admin"] = True
        if self.type == "link":
            payload["link"] = self.link
        return payload

    def summary(self) -> str:
        """一行摘要，用于 preview / list 输出。"""
        bits = [f"[{'指令' if self.type == 'command' else '链接'}] {self.name}"]
        if self.desc:
            bits.append(f"- {self.desc}")
        if self.type == "link":
            bits.append(f"→ {self.link}")
        if self.only_admin:
            bits.append("(仅群管理员可见)")
        return " ".join(bits)


@dataclass(slots=True)
class PanelSpec:
    """一个由本插件托管的指令面板。

    ``key`` 是插件本地的标识，用来把配置和平台返回的 ``panel_id`` 对应起来，
    改配置时不会重复建面板。它不会发给平台。
    """

    key: str
    scope: str
    items: list[PanelItem]
    target_type: str = "all"
    user_openids: list[str] = field(default_factory=list)
    group_openids: list[str] = field(default_factory=list)
    remark: str = ""
    enabled: bool = True

    @classmethod
    def from_dict(cls, raw: Any, index: int) -> PanelSpec:
        """解析并校验单个面板配置。

        Args:
            raw: 配置里的面板对象。
            index: 在数组中的下标，用于出错定位。

        Returns:
            解析后的面板配置。

        Raises:
            ConfigError: 任一字段不合法。
        """
        if not isinstance(raw, dict):
            raise ConfigError(f"panels[{index}]: 必须是对象，收到 {type(raw).__name__}")

        key = str(raw.get("key") or "").strip()
        if not key:
            raise ConfigError(f"panels[{index}]: key 不能为空（用于记住已创建的面板）")
        where = f"panels[{index}] (key={key})"

        scope = str(raw.get("scope") or "").strip()
        if scope not in SCOPES:
            raise ConfigError(f"{where}: scope 必须是 {'/'.join(SCOPES)}，收到 {scope!r}")

        target_type = str(raw.get("target_type") or "all").strip()
        if target_type not in TARGET_TYPES:
            raise ConfigError(
                f"{where}: target_type 必须是 {'/'.join(TARGET_TYPES)}，收到 {target_type!r}"
            )
        if target_type == "specific" and scope not in SCOPES_SUPPORTING_SPECIFIC:
            raise ConfigError(
                f"{where}: scope={scope} 只支持 target_type=all"
                f"（仅 {'/'.join(SCOPES_SUPPORTING_SPECIFIC)} 支持 specific）"
            )

        user_openids = _str_list(raw.get("user_openids"), f"{where}.user_openids")
        group_openids = _str_list(raw.get("group_openids"), f"{where}.group_openids")
        if target_type == "all" and (user_openids or group_openids):
            raise ConfigError(
                f"{where}: target_type=all 不能指定 openid，请改成 specific"
            )
        if target_type == "specific":
            if scope == "c2c":
                if group_openids:
                    raise ConfigError(f"{where}: c2c 场景请用 user_openids")
                if not user_openids:
                    raise ConfigError(f"{where}: c2c + specific 需要至少一个 user_openids")
            else:
                if user_openids:
                    raise ConfigError(f"{where}: group 场景请用 group_openids")
                if not group_openids:
                    raise ConfigError(f"{where}: group + specific 需要至少一个 group_openids")
        for name, ids in (("user_openids", user_openids), ("group_openids", group_openids)):
            if len(ids) > MAX_OPENIDS_PER_PANEL:
                raise ConfigError(
                    f"{where}: {name} 最多 {MAX_OPENIDS_PER_PANEL} 个，收到 {len(ids)} 个"
                )

        remark = str(raw.get("remark") or "").strip()
        if len(remark) > MAX_REMARK_LEN:
            raise ConfigError(f"{where}: remark 最多 {MAX_REMARK_LEN} 个字符")

        raw_items = raw.get("items")
        if raw_items is None:
            raw_items = []
        if not isinstance(raw_items, list):
            raise ConfigError(f"{where}: items 必须是数组")
        if len(raw_items) > MAX_PANEL_ITEMS:
            raise ConfigError(
                f"{where}: items 最多 {MAX_PANEL_ITEMS} 个，收到 {len(raw_items)} 个"
            )
        items = [
            PanelItem.from_dict(item, f"{where}.items[{i}]")
            for i, item in enumerate(raw_items)
        ]

        return cls(
            key=key,
            scope=scope,
            items=items,
            target_type=target_type,
            user_openids=user_openids,
            group_openids=group_openids,
            remark=remark,
            enabled=bool(raw.get("enabled", True)),
        )

    def panel_payload(self) -> dict[str, Any]:
        """转换成 API 请求体里的 Panel 对象。"""
        payload: dict[str, Any] = {"items": [i.to_payload() for i in self.items]}
        if self.remark:
            payload["remark"] = self.remark
        return payload

    @property
    def desired_openids(self) -> list[str]:
        """该面板期望关联的 openid（按 scope 取对应的那一组）。"""
        return self.user_openids if self.scope == "c2c" else self.group_openids

    def summary(self) -> str:
        """多行摘要，用于 preview 输出。"""
        head = f"● {SCOPE_LABELS.get(self.scope, self.scope)}面板"
        if not self.key.startswith(AUTO_KEY_PREFIX):
            head += f"（额外面板 key={self.key}）"
        if not self.enabled:
            head += " (已禁用)"
        if self.target_type == "specific":
            head += f"（只对 {len(self.desired_openids)} 个指定对象生效）"
        lines = [head]
        lines.extend(f"    {i + 1}. {item.summary()}" for i, item in enumerate(self.items))
        if self.target_type == "specific":
            ids = self.desired_openids
            preview = "、".join(ids[:3]) + ("…" if len(ids) > 3 else "")
            lines.append(f"    关联对象：{preview}")
        return "\n".join(lines)


# ------------------------------------------------------------------ 自定义菜单


@dataclass(slots=True)
class MenuItem:
    """自定义菜单项。一级菜单支持 4 种类型，二级菜单只支持 2 种。"""

    type: str
    name: str
    send_message: str = ""
    link: str = ""
    switch_id: str = ""
    switch_default: bool = False
    sub_menu_items: list[MenuItem] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Any, where: str, *, is_sub: bool = False) -> MenuItem:
        """解析并校验单个菜单项。

        Args:
            raw: 配置里的菜单项对象。
            where: 出错时用于定位的路径描述。
            is_sub: 是否是二级菜单项（限制更严）。

        Returns:
            解析后的菜单项。

        Raises:
            ConfigError: 字段类型、取值或长度不合法。
        """
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: 菜单项必须是对象，收到 {type(raw).__name__}")

        allowed = SUB_MENU_ITEM_TYPES if is_sub else MENU_ITEM_TYPES
        item_type = str(raw.get("type") or "").strip()
        if item_type not in allowed:
            extra = "（二级菜单不支持 switch 和 menu）" if is_sub else ""
            raise ConfigError(
                f"{where}: type 必须是 {'/'.join(allowed)}{extra}，收到 {item_type!r}"
            )

        max_width = (
            MAX_SUB_MENU_ITEM_NAME_WIDTH if is_sub else MAX_MENU_ITEM_NAME_WIDTH
        )
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ConfigError(f"{where}: name 不能为空")
        if text_width(name) > max_width:
            raise ConfigError(
                f"{where}: name {name!r} 过长（{text_width(name)} > {max_width} 字符，"
                f"汉字算 2 个）"
            )

        send_message = str(raw.get("send_message") or "")
        link = str(raw.get("link") or "").strip()
        switch_id = ""
        switch_default = False
        sub_items: list[MenuItem] = []

        if item_type == "send_message":
            if not send_message:
                raise ConfigError(f"{where}: type=send_message 必须提供 send_message")
        elif item_type == "link":
            if not link:
                raise ConfigError(f"{where}: type=link 必须提供 link")
            if not link.startswith("https://"):
                raise ConfigError(f"{where}: link 必须以 https:// 开头，收到 {link!r}")
        elif item_type == "switch":
            switch = raw.get("switch")
            if not isinstance(switch, dict):
                raise ConfigError(f"{where}: type=switch 必须提供 switch 对象")
            switch_id = str(switch.get("switch_id") or "").strip()
            if not switch_id:
                raise ConfigError(f"{where}: switch.switch_id 不能为空")
            switch_default = bool(switch.get("default", False))
        else:  # menu
            raw_subs = raw.get("sub_menu_items")
            if not isinstance(raw_subs, list) or not raw_subs:
                raise ConfigError(f"{where}: type=menu 必须提供非空的 sub_menu_items")
            if len(raw_subs) > MAX_SUB_MENU_ITEMS:
                raise ConfigError(
                    f"{where}: sub_menu_items 最多 {MAX_SUB_MENU_ITEMS} 个，"
                    f"收到 {len(raw_subs)} 个"
                )
            sub_items = [
                cls.from_dict(sub, f"{where}.sub_menu_items[{i}]", is_sub=True)
                for i, sub in enumerate(raw_subs)
            ]

        return cls(
            type=item_type,
            name=name,
            send_message=send_message,
            link=link,
            switch_id=switch_id,
            switch_default=switch_default,
            sub_menu_items=sub_items,
        )

    def to_payload(self) -> dict[str, Any]:
        """转换成 API 请求体里的 MenuItem / SubMenuItem。"""
        payload: dict[str, Any] = {"type": self.type, "name": self.name}
        if self.type == "send_message":
            payload["send_message"] = self.send_message
        elif self.type == "link":
            payload["link"] = self.link
        elif self.type == "switch":
            payload["switch"] = {
                "switch_id": self.switch_id,
                "default": self.switch_default,
            }
        else:
            payload["sub_menu_items"] = [s.to_payload() for s in self.sub_menu_items]
        return payload

    def summary(self, indent: int = 0) -> str:
        """多行摘要，用于 preview / menu show 输出。"""
        pad = "    " * indent
        kind = MENU_TYPE_LABELS.get(self.type, self.type)
        detail = ""
        if self.type == "send_message":
            detail = f" → 发送 {self.send_message}"
        elif self.type == "link":
            detail = f" → {self.link}"
        elif self.type == "switch":
            state = "默认开" if self.switch_default else "默认关"
            detail = f" → 开关标识 {self.switch_id}（{state}）"
        lines = [f"{pad}[{kind}] {self.name}{detail}"]
        lines.extend(s.summary(indent + 1) for s in self.sub_menu_items)
        return "\n".join(lines)


@dataclass(slots=True)
class MenuSpec:
    """单聊自定义菜单（全局生效，不区分用户）。"""

    items: list[MenuItem] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Any) -> MenuSpec:
        """解析并校验菜单配置。

        Args:
            raw: ``{"items": [...]}``，也接受直接给 items 数组。

        Returns:
            解析后的菜单配置。

        Raises:
            ConfigError: 任一字段不合法。
        """
        if isinstance(raw, list):
            raw = {"items": raw}
        if not isinstance(raw, dict):
            raise ConfigError(f"menu: 必须是对象，收到 {type(raw).__name__}")

        # 兼容直接把平台返回体贴进配置的情况（外层多一层 menu）。
        if "items" not in raw and isinstance(raw.get("menu"), dict):
            raw = raw["menu"]

        raw_items = raw.get("items")
        if raw_items is None:
            raw_items = []
        if not isinstance(raw_items, list):
            raise ConfigError("menu.items: 必须是数组")
        if len(raw_items) > MAX_MENU_ITEMS:
            raise ConfigError(
                f"menu.items: 一级菜单最多 {MAX_MENU_ITEMS} 个，收到 {len(raw_items)} 个"
            )
        items = [
            MenuItem.from_dict(item, f"menu.items[{i}]")
            for i, item in enumerate(raw_items)
        ]
        return cls(items=items)

    def menu_payload(self) -> dict[str, Any]:
        """转换成 API 请求体里的 Menu 对象。"""
        return {"items": [i.to_payload() for i in self.items]}

    def summary(self) -> str:
        """多行摘要，用于 preview 输出。"""
        if not self.items:
            return "（空菜单）"
        return "\n".join(i.summary() for i in self.items)


# ------------------------------------------------------------------ 入口


def parse_panels(raw: Any, where: str = "advanced_panels_json") -> list[PanelSpec]:
    """解析 JSON 形式的面板配置（高级用法）。

    Args:
        raw: JSON 文本、面板数组，或 ``{"panels": [...]}``。
        where: 出错时提示的配置项名。

    Returns:
        面板配置列表（含被禁用的项）。

    Raises:
        ConfigError: JSON 无法解析，或存在不合法项；多个问题会一次性列出。
    """
    data = _load_json(raw, where)
    if data in (None, ""):
        return []
    if isinstance(data, dict):
        if "panels" not in data:
            raise ConfigError(
                f"{where}: 顶层是对象时必须有 panels 字段，也可以直接写成 [...] 数组"
            )
        data = data["panels"]
    if not isinstance(data, list):
        raise ConfigError(f'{where}: 顶层必须是数组，或 {{"panels": [...]}} 对象')

    specs: list[PanelSpec] = []
    errors: list[str] = []
    for index, item in enumerate(data):
        try:
            specs.append(PanelSpec.from_dict(item, index))
        except ConfigError as e:
            errors.append(f"{where} 里 {e}")

    seen: dict[str, int] = {}
    for i, spec in enumerate(specs):
        if spec.key in seen:
            errors.append(
                f"{where}: panels[{i}] 的 key {spec.key!r} 与 panels[{seen[spec.key]}] 重复"
            )
        else:
            seen[spec.key] = i

    enabled = sum(1 for s in specs if s.enabled)
    if enabled > MAX_PANELS:
        errors.append(f"{where}: 启用的面板 {enabled} 个，超过平台上限 {MAX_PANELS} 个")

    if errors:
        raise ConfigError("\n".join(errors))
    return specs


def parse_menu(raw: Any, where: str = "advanced_menu_json") -> MenuSpec:
    """解析 JSON 形式的菜单配置（高级用法）。

    Args:
        raw: JSON 文本、``{"items": [...]}`` 或 items 数组。
        where: 出错时提示的配置项名。

    Returns:
        菜单配置；配置为空时返回空菜单。

    Raises:
        ConfigError: JSON 无法解析，或存在不合法项。
    """
    data = _load_json(raw, where)
    if data in (None, ""):
        return MenuSpec()
    return MenuSpec.from_dict(data)


# ------------------------------------------------------------------ 可视化配置

PANEL_KIND_LABELS = {"command": "指令按钮", "link": "链接按钮"}
MENU_KIND_LABELS = {
    "command": "发送指令",
    "link": "打开链接",
    "folder": "折叠菜单",
    "switch": "开关",
}


def build_panels(
    entries: Any,
    *,
    user_openids: Any = None,
    group_openids: Any = None,
) -> list[PanelSpec]:
    """把 WebUI 里「指令面板按钮」的条目列表编成若干个面板。

    每个条目自己声明在哪些聊天场景显示，这里按场景归拢：同一个场景的按钮
    合成一个面板，顺序就是配置里的顺序。一个场景都没勾的条目会被跳过，
    相当于临时停用。

    Args:
        entries: ``panel_items`` 的值（条目数组）。
        user_openids: 单聊面板的白名单 openid，留空表示对所有人生效。
        group_openids: 群聊面板的白名单 openid，留空表示对所有群生效。

    Returns:
        面板配置列表，按 c2c / group / channel / dm 排序。

    Raises:
        ConfigError: 任一条目不合法；所有问题会一次性列出。
    """
    rows = _entry_rows(entries, "指令面板按钮")
    errors: list[str] = []
    by_scope: dict[str, list[PanelItem]] = {scope: [] for scope in SCOPES}

    for index, entry in enumerate(rows):
        where = f"「指令面板按钮」第 {index + 1} 条"
        if not isinstance(entry, dict):
            errors.append(f"{where}：配置坏了（不是一个对象），删掉重新添加即可")
            continue
        kind = _entry_kind(entry)
        if kind not in PANEL_ENTRY_KINDS:
            errors.append(
                f"{where}：认不出这是什么按钮（{kind or '未选择模板'}），删掉重新添加即可"
            )
            continue
        where = f"{where}（{PANEL_KIND_LABELS[kind]}）"

        item, item_errors = _panel_entry(kind, entry, where)
        errors.extend(item_errors)
        scopes, scope_errors = _scope_list(entry.get("scopes"), where)
        errors.extend(scope_errors)
        if item is None:
            continue
        for scope in scopes:
            by_scope[scope].append(item)

    users, user_errors = _openid_list(user_openids, "单聊面板只对这些用户生效")
    groups, group_errors = _openid_list(group_openids, "群聊面板只对这些群生效")
    errors.extend(user_errors)
    errors.extend(group_errors)

    specs: list[PanelSpec] = []
    for scope in SCOPES:
        items = by_scope[scope]
        if not items:
            continue
        if len(items) > MAX_PANEL_ITEMS:
            errors.append(
                f"「指令面板按钮」：{SCOPE_LABELS[scope]}有 {len(items)} 个按钮，"
                f"超过平台上限 {MAX_PANEL_ITEMS} 个"
            )
            continue
        target_type = "all"
        spec_users: list[str] = []
        spec_groups: list[str] = []
        if scope == "c2c" and users:
            target_type, spec_users = "specific", users
        elif scope == "group" and groups:
            target_type, spec_groups = "specific", groups
        specs.append(
            PanelSpec(
                key=f"{AUTO_KEY_PREFIX}{scope}",
                scope=scope,
                items=items,
                target_type=target_type,
                user_openids=spec_users,
                group_openids=spec_groups,
                remark=f"AstrBot 插件自动生成：{SCOPE_LABELS[scope]}",
            )
        )

    if errors:
        raise ConfigError("\n".join(errors))
    return specs


def build_menu(entries: Any) -> MenuSpec:
    """把 WebUI 里「单聊底部菜单」的条目列表编成菜单。

    条目是平铺的，靠「放进哪个折叠菜单」指定归属，这里把它们还原成
    一级 / 二级两层结构，一级顺序就是配置里的顺序。

    Args:
        entries: ``menu_items`` 的值（条目数组）。

    Returns:
        菜单配置。

    Raises:
        ConfigError: 任一条目不合法；所有问题会一次性列出。
    """
    rows = _entry_rows(entries, "单聊底部菜单")
    errors: list[str] = []

    parsed: list[tuple[int, str, dict[str, Any]]] = []
    for index, entry in enumerate(rows):
        where = f"「单聊底部菜单」第 {index + 1} 条"
        if not isinstance(entry, dict):
            errors.append(f"{where}：配置坏了（不是一个对象），删掉重新添加即可")
            continue
        kind = _entry_kind(entry)
        if kind not in MENU_ENTRY_KINDS:
            errors.append(
                f"{where}：认不出这是什么条目（{kind or '未选择模板'}），删掉重新添加即可"
            )
            continue
        parsed.append((index, kind, entry))

    # 先建好折叠菜单，后面的条目才知道能往哪儿放。
    folders: dict[str, MenuItem] = {}
    top: dict[int, MenuItem] = {}
    for index, kind, entry in parsed:
        if kind != "folder":
            continue
        where = f"「单聊底部菜单」第 {index + 1} 条（折叠菜单）"
        label = str(entry.get("label") or "").strip()
        if not label:
            errors.append(f"{where}：「菜单文字」不能为空")
            continue
        if label in folders:
            errors.append(
                f"{where}：已经有一个叫「{label}」的折叠菜单了，"
                f"请改个不一样的名字，否则不知道该往哪个里面放"
            )
            continue
        error = _width_error(label, MAX_MENU_ITEM_NAME_WIDTH, where, "菜单文字")
        if error:
            errors.append(error)
            continue
        item = MenuItem(type="menu", name=label)
        folders[label] = item
        top[index] = item

    for index, kind, entry in parsed:
        if kind == "folder":
            continue
        where = f"「单聊底部菜单」第 {index + 1} 条（{MENU_KIND_LABELS[kind]}）"
        parent = str(entry.get("parent") or "").strip()
        if parent and kind == "switch":
            errors.append(f"{where}：开关只能放在一级菜单，不能放进折叠菜单里")
            continue
        if parent and parent not in folders:
            available = "、".join(folders) or "（还没有折叠菜单）"
            errors.append(
                f"{where}：找不到叫「{parent}」的折叠菜单。"
                f"现有的折叠菜单：{available}"
            )
            continue

        label = str(entry.get("label") or "").strip()
        if not label:
            errors.append(f"{where}：「菜单文字」不能为空")
            continue
        limit = MAX_SUB_MENU_ITEM_NAME_WIDTH if parent else MAX_MENU_ITEM_NAME_WIDTH
        error = _width_error(label, limit, where, "菜单文字")
        if error:
            errors.append(error)
            continue

        item, item_errors = _menu_entry(kind, entry, where, label)
        errors.extend(item_errors)
        if item is None:
            continue
        if parent:
            folders[parent].sub_menu_items.append(item)
        else:
            top[index] = item

    for label, folder in folders.items():
        if not folder.sub_menu_items:
            errors.append(
                f"「单聊底部菜单」：折叠菜单「{label}」里还没有条目。"
                f"把某个条目的「放进哪个折叠菜单」填成「{label}」，或者把它删掉"
            )
        elif len(folder.sub_menu_items) > MAX_SUB_MENU_ITEMS:
            errors.append(
                f"「单聊底部菜单」：折叠菜单「{label}」里有 "
                f"{len(folder.sub_menu_items)} 个条目，超过平台上限 "
                f"{MAX_SUB_MENU_ITEMS} 个"
            )

    items = [top[index] for index in sorted(top)]
    if len(items) > MAX_MENU_ITEMS:
        errors.append(
            f"「单聊底部菜单」：一级菜单有 {len(items)} 个条目，"
            f"超过平台上限 {MAX_MENU_ITEMS} 个（折叠菜单里的不算）"
        )

    if errors:
        raise ConfigError("\n".join(errors))
    return MenuSpec(items=items)


def load_panels(
    entries: Any,
    *,
    user_openids: Any = None,
    group_openids: Any = None,
    advanced_json: Any = None,
) -> list[PanelSpec]:
    """合并可视化配置与高级 JSON 配置，得到最终要推送的面板列表。

    Args:
        entries: ``panel_items`` 的值。
        user_openids: ``target_user_openids`` 的值。
        group_openids: ``target_group_openids`` 的值。
        advanced_json: ``advanced_panels_json`` 的值，留空表示不用。

    Returns:
        面板配置列表。

    Raises:
        ConfigError: 任一处配置不合法；所有问题会一次性列出。
    """
    errors: list[str] = []
    specs: list[PanelSpec] = []
    try:
        specs = build_panels(
            entries,
            user_openids=user_openids,
            group_openids=group_openids,
        )
    except ConfigError as e:
        errors.append(str(e))

    extra: list[PanelSpec] = []
    try:
        extra = parse_panels(advanced_json)
    except ConfigError as e:
        errors.append(str(e))

    seen = {spec.key for spec in specs}
    for spec in extra:
        if spec.key.startswith(AUTO_KEY_PREFIX):
            errors.append(
                f"advanced_panels_json: key {spec.key!r} 不能以 "
                f"{AUTO_KEY_PREFIX!r} 开头，那是可视化配置自动生成的面板在用的"
            )
            continue
        if spec.key in seen:
            errors.append(f"advanced_panels_json: key {spec.key!r} 重复了")
            continue
        seen.add(spec.key)
        specs.append(spec)

    enabled = sum(1 for spec in specs if spec.enabled)
    if enabled > MAX_PANELS:
        errors.append(f"一共要推送 {enabled} 个面板，超过平台上限 {MAX_PANELS} 个")

    if errors:
        raise ConfigError("\n".join(errors))
    return specs


def load_menu(entries: Any, *, advanced_json: Any = None) -> MenuSpec:
    """得到最终要推送的单聊菜单。

    ``advanced_menu_json`` 一旦非空就完全取代可视化配置——菜单是整体覆盖的，
    没法把两份配置拼在一起。

    Args:
        entries: ``menu_items`` 的值。
        advanced_json: ``advanced_menu_json`` 的值，留空表示不用。

    Returns:
        菜单配置。

    Raises:
        ConfigError: 配置不合法。
    """
    advanced = _load_json(advanced_json, "advanced_menu_json")
    if advanced not in (None, ""):
        return MenuSpec.from_dict(advanced)
    return build_menu(entries)


def _entry_rows(entries: Any, label: str) -> list[Any]:
    """把条目列表规整成数组。"""
    if entries in (None, ""):
        return []
    if not isinstance(entries, list):
        raise ConfigError(f"「{label}」的配置坏了（应该是一个列表），请在 WebUI 里重新配置")
    return entries


def _entry_kind(entry: dict[str, Any]) -> str:
    """取出条目选的模板名。"""
    return str(entry.get("__template_key") or entry.get("template") or "").strip()


def _width_error(value: str, limit: int, where: str, label: str) -> str | None:
    """长度超限时返回一句人话提示，否则返回 None。"""
    width = text_width(value)
    if width > limit:
        return (
            f"{where}：「{label}」{value!r} 太长了"
            f"（{width} > {limit} 个字符，一个汉字算 2 个）"
        )
    return None


def _panel_entry(
    kind: str,
    entry: dict[str, Any],
    where: str,
) -> tuple[PanelItem | None, list[str]]:
    """把一条「指令面板按钮」配置转成面板元素。"""
    errors: list[str] = []
    desc = str(entry.get("desc") or "").strip()
    error = _width_error(desc, MAX_PANEL_ITEM_DESC_WIDTH, where, "说明文字")
    if error:
        errors.append(error)

    if kind == "command":
        name = str(entry.get("command") or "").strip()
        if not name:
            errors.append(f"{where}：「指令」不能为空，比如填 /help")
            return None, errors
        error = _width_error(name, MAX_PANEL_ITEM_NAME_WIDTH, where, "指令")
        if error:
            errors.append(error)
            return None, errors
        item = PanelItem(
            type="command",
            name=name,
            desc=desc,
            only_admin=bool(entry.get("only_admin", False)),
        )
    else:
        name = str(entry.get("title") or "").strip()
        if not name:
            errors.append(f"{where}：「按钮文字」不能为空")
            return None, errors
        error = _width_error(name, MAX_PANEL_ITEM_NAME_WIDTH, where, "按钮文字")
        if error:
            errors.append(error)
            return None, errors
        url = str(entry.get("url") or "").strip()
        if not url:
            errors.append(f"{where}：「网址」不能为空")
            return None, errors
        if not url.startswith("https://"):
            errors.append(f"{where}：「网址」必须以 https:// 开头，现在是 {url!r}")
            return None, errors
        item = PanelItem(
            type="link",
            name=name,
            desc=desc,
            only_admin=bool(entry.get("only_admin", False)),
            link=url,
        )
    return item, errors


def _menu_entry(
    kind: str,
    entry: dict[str, Any],
    where: str,
    label: str,
) -> tuple[MenuItem | None, list[str]]:
    """把一条「单聊底部菜单」配置转成菜单项（折叠菜单除外）。"""
    if kind == "command":
        command = str(entry.get("command") or "")
        if not command.strip():
            return None, [f"{where}：「要发送的指令」不能为空，比如填 /help"]
        return MenuItem(type="send_message", name=label, send_message=command), []

    if kind == "link":
        url = str(entry.get("url") or "").strip()
        if not url:
            return None, [f"{where}：「网址」不能为空"]
        if not url.startswith("https://"):
            return None, [f"{where}：「网址」必须以 https:// 开头，现在是 {url!r}"]
        return MenuItem(type="link", name=label, link=url), []

    switch_id = str(entry.get("switch_id") or "").strip()
    if not switch_id:
        return None, [f"{where}：「开关标识」不能为空，比如填 search"]
    return (
        MenuItem(
            type="switch",
            name=label,
            switch_id=switch_id,
            switch_default=bool(entry.get("default_on", False)),
        ),
        [],
    )


def _scope_list(raw: Any, where: str) -> tuple[list[str], list[str]]:
    """解析「在哪些聊天里显示」。返回（场景列表，错误列表）。"""
    if raw in (None, ""):
        return [], []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return [], [f"{where}：「在哪些聊天里显示」的配置坏了，请重新勾选"]

    scopes: list[str] = []
    errors: list[str] = []
    for value in raw:
        scope = str(value).strip()
        if not scope:
            continue
        if scope not in SCOPES:
            errors.append(
                f"{where}：认不出聊天场景 {scope!r}，可选：{'/'.join(SCOPES)}"
            )
            continue
        if scope not in scopes:
            scopes.append(scope)
    return scopes, errors


def _openid_list(raw: Any, label: str) -> tuple[list[str], list[str]]:
    """解析 openid 白名单。返回（openid 列表，错误列表）。"""
    try:
        ids = _str_list(raw, label)
    except ConfigError:
        return [], [
            f"「{label}」的配置坏了（应该是一列 openid），请在 WebUI 里重新填写"
        ]
    if len(ids) > MAX_OPENIDS_PER_PANEL:
        return [], [
            f"「{label}」填了 {len(ids)} 个，超过平台上限 {MAX_OPENIDS_PER_PANEL} 个"
        ]
    return ids, []


def _load_json(raw: Any, where: str) -> Any:
    """把配置值统一成 Python 对象。字符串按 JSON 解析，其余原样返回。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ConfigError(
                f"{where}: JSON 解析失败（第 {e.lineno} 行第 {e.colno} 列）：{e.msg}"
            ) from e
    return raw


def _str_list(raw: Any, where: str) -> list[str]:
    """把配置值规整成去重后的非空字符串列表。"""
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise ConfigError(f"{where}: 必须是字符串数组")
    result: list[str] = []
    for item in raw:
        value = str(item).strip()
        if value and value not in result:
            result.append(value)
    return result
