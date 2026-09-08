"""QQ 官方机器人「指令面板 / 自定义菜单」AstrBot 插件。"""

from .api import QQBotOpenAPI, QQOpenAPIError
from .models import (
    ConfigError,
    MenuItem,
    MenuSpec,
    PanelItem,
    PanelSpec,
    build_menu,
    build_panels,
    load_menu,
    load_panels,
    parse_menu,
    parse_panels,
    text_width,
)
from .sync import PanelStateStore, SyncReport, sync_menu, sync_panels

__all__ = [
    "ConfigError",
    "MenuItem",
    "MenuSpec",
    "PanelItem",
    "PanelSpec",
    "PanelStateStore",
    "QQBotOpenAPI",
    "QQOpenAPIError",
    "SyncReport",
    "build_menu",
    "build_panels",
    "load_menu",
    "load_panels",
    "parse_menu",
    "parse_panels",
    "sync_menu",
    "sync_panels",
    "text_width",
]
