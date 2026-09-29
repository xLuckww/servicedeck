"""菜单栏菜单的内容（纯数据，不依赖 AppKit，便于测试）。menubar.py 负责把它画出来（设计 8）。"""

from dataclasses import dataclass, field

from servicedeck.status import Summary

DOT = {"green": "🟢", "yellow": "🟡", "red": "🔴", "grey": "⚪"}


@dataclass
class Item:
    title: str
    action: str | None = None  # 点击后执行的动作名，None 表示不可点（标题行）
    service_id: str | None = None
    enabled: bool = True
    checked: bool = False
    separator: bool = False
    children: list["Item"] = field(default_factory=list)


SEP = Item("", separator=True)


def icon(summary: Summary | None) -> tuple[str, str]:
    """(圆点颜色, 文字)。清单有误时为红色。"""
    if summary is None:
        return "red", "SD"
    return summary.overall, "SD"


def _settings_item(settings: dict) -> Item:
    return Item("设置", children=[
        Item("登录时启动", action="toggle_login_launch", checked=bool(settings.get("login_launch"))),
        Item("开机自动启动服务", action="toggle_boot_autostart", checked=bool(settings.get("boot_autostart"))),
    ])


def _tail(settings: dict) -> list[Item]:
    return [SEP, _settings_item(settings), Item("退出 ServiceDeck", action="quit")]


def build(summary: Summary | None, settings: dict, error: str | None = None) -> list[Item]:
    if summary is None:
        return [Item(f"服务清单有误：{error or ''}", enabled=False), Item("打开控制台", action="open_console"),
                *_tail(settings)]
    if not summary.manager_running:
        return [Item("服务管理器未运行", enabled=False), Item("打开控制台", action="open_console"),
                Item("启动全部服务", action="start_all"), *_tail(settings)]

    head = f"运行中 {summary.running_count} 个"
    if summary.error_count:
        head += f" · 异常 {summary.error_count} 个"
    items = [Item(head, enabled=False), Item("打开控制台", action="open_console"),
             Item("全部启动", action="start_all"), Item("全部停止", action="stop_all")]
    for g in summary.groups:
        if not g.services:
            continue
        items.append(SEP)
        items.append(Item(f"{g.name}  {g.running}/{g.total}", enabled=False))
        for s in g.services:
            extra = s.label if s.display != "healthy" else (f"端口 {s.port}" if s.port else s.label)
            active = s.display in ("healthy", "starting", "stopping")
            children = [Item("停止", action="stop", service_id=s.id) if active
                        else Item("启动", action="start", service_id=s.id),
                        Item("重启", action="restart", service_id=s.id)]
            if s.port:
                children.append(Item("打开网页", action="open_web", service_id=s.id, enabled=s.display == "healthy"))
            children.append(Item("查看日志", action="open_log", service_id=s.id))
            items.append(Item(f"{DOT[s.color]} {s.name}  {extra}", service_id=s.id, children=children))
    return items + _tail(settings)
