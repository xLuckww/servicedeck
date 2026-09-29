"""菜单栏图标与菜单（设计 8）。菜单内容来自 menubar_model，这里只负责画出来和响应点击。

点击后的操作在后台线程执行（不卡住界面），需要确认时弹出系统对话框，失败时弹出原因。
"""

import logging
import threading

import objc
from AppKit import (
    NSAlert,
    NSApplication,
    NSAttributedString,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMenu,
    NSMenuItem,
    NSMutableAttributedString,
    NSStatusBar,
    NSVariableStatusItemLength,
    NSScreen,
    NSWorkspace,
)
from Foundation import NSNotificationCenter, NSObject, NSUserDefaults
from PyObjCTools import AppHelper

from servicedeck import actions
from servicedeck.app import menubar_model, osutil, settings

log = logging.getLogger(__name__)

AUTOSAVE_NAME = "ServiceDeck"
INITIAL_POSITION = 290.0  # 约在 Wi-Fi 图标左侧，刘海屏上也在可见区域内

COLORS = {
    "green": lambda: NSColor.systemGreenColor(),
    "yellow": lambda: NSColor.systemYellowColor(),
    "red": lambda: NSColor.systemRedColor(),
    "grey": lambda: NSColor.systemGrayColor(),
}


class MenuController(NSObject):
    """接收菜单点击和"App 被激活"通知（Objective-C 对象，方法名即选择器）。"""

    owner = None

    def menuAction_(self, sender):
        self.owner.handle(sender.tag())

    def appActivated_(self, note):
        self.owner.window.on_app_activated()

    def willPowerOff_(self, note):
        # 关机、重启、注销时放行退出，否则"关闭窗口 = 隐藏"的拦截会挡住系统
        self.owner.window.quitting = True


class MenuBar:
    def __init__(self, window_ctrl, refresh_now):
        self.window = window_ctrl
        self.refresh_now = refresh_now  # 操作完成后立即刷新一次状态
        self.controller = MenuController.alloc().init()
        self.controller.owner = self
        self.item = None
        self.actions_by_tag: dict[int, tuple[str, str | None]] = {}
        self.last_icon = ("grey", "SD")
        self.last_titles: list[str] = []

    # ---------- 主线程 ----------

    def setup(self) -> None:
        # 刘海屏上菜单栏放不下的图标会被藏到刘海后面，而新图标默认排在最靠近刘海的位置。
        # 第一次运行时给一个靠右的初始位置（数值 = 离屏幕右边的距离），之后用户按住 ⌘ 拖动的位置会被系统记住。
        defaults = NSUserDefaults.standardUserDefaults()
        key = f"NSStatusItem Preferred Position {AUTOSAVE_NAME}"
        if defaults.objectForKey_(key) is None:
            defaults.setDouble_forKey_(INITIAL_POSITION, key)
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self.item.setAutosaveName_(AUTOSAVE_NAME)
        self.item.setVisible_(True)
        self.set_icon("grey")
        self.item.setMenu_(NSMenu.alloc().init())
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self.controller, "appActivated:", "NSApplicationDidBecomeActiveNotification", None
        )
        NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            self.controller, "willPowerOff:", "NSWorkspaceWillPowerOffNotification", None
        )

    def status_item_info(self) -> dict:
        """菜单栏图标的位置，以及是否落在屏幕的可见区域（没被刘海挡住）。"""
        info = {"visible": bool(self.item and self.item.isVisible()),
                "autosave": str(self.item.autosaveName()) if self.item else None}
        win = self.item.button().window() if self.item else None
        if win is not None:
            f = win.frame()
            info["x"], info["width"] = float(f.origin.x), float(f.size.width)
            screen = win.screen() or NSScreen.mainScreen()
            right = screen.auxiliaryTopRightArea() if screen and hasattr(screen, "auxiliaryTopRightArea") else None
            if right is not None and right.size.width > 0:
                info["on_screen"] = f.origin.x >= right.origin.x - 1
            else:
                info["on_screen"] = True  # 没有刘海的屏幕
            info["occlusion_visible"] = bool(win.occlusionState() & 2)
        return info

    def log_status_item(self) -> None:
        info = self.status_item_info()
        log.info("菜单栏图标：%s", info)
        if info.get("on_screen") is False or info.get("occlusion_visible") is False:
            log.warning("菜单栏图标可能被刘海或菜单栏管理工具挡住了：可按住 ⌘ 把其他图标拖走，或在系统设置 → 菜单栏里检查")

    def set_icon(self, color: str) -> None:
        font = NSFont.menuBarFontOfSize_(0)
        title = NSMutableAttributedString.alloc().init()
        title.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
            "●", {NSForegroundColorAttributeName: COLORS[color](), NSFontAttributeName: font}))
        title.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
            " SD", {NSFontAttributeName: font}))
        self.item.button().setAttributedTitle_(title)
        self.last_icon = (color, "SD")

    def render(self, summary, error: str | None = None) -> None:
        color, _ = menubar_model.icon(summary)
        self.set_icon(color)
        model = menubar_model.build(summary, settings.load(), error)
        self.actions_by_tag = {}
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        for it in model:
            menu.addItem_(self._make(it))
        self.item.setMenu_(menu)
        self.last_titles = [i.title for i in model if not i.separator]

    def _make(self, it):
        if it.separator:
            return NSMenuItem.separatorItem()
        mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(it.title, None, "")
        mi.setEnabled_(it.enabled)
        if it.checked:
            mi.setState_(1)
        if it.action:
            tag = len(self.actions_by_tag) + 1
            self.actions_by_tag[tag] = (it.action, it.service_id)
            mi.setTag_(tag)
            mi.setTarget_(self.controller)
            mi.setAction_("menuAction:")
        if it.children:
            sub = NSMenu.alloc().init()
            sub.setAutoenablesItems_(False)
            for c in it.children:
                sub.addItem_(self._make(c))
            mi.setSubmenu_(sub)
        return mi

    def alert(self, title: str, text: str = "", confirm: bool = False) -> bool:
        """弹出系统对话框；confirm=True 时有"确定/取消"，返回是否确定。"""
        self.window.suppress_activation += 1
        try:
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            a = NSAlert.alloc().init()
            a.setMessageText_(title)
            a.setInformativeText_(text)
            a.addButtonWithTitle_("确定")
            if confirm:
                a.addButtonWithTitle_("取消")
            return a.runModal() == 1000  # NSAlertFirstButtonReturn
        finally:
            AppHelper.callLater(0.5, self._release_suppress)

    def _release_suppress(self):
        self.window.suppress_activation = max(0, self.window.suppress_activation - 1)

    def handle(self, tag: int) -> None:
        action, sid = self.actions_by_tag.get(tag, (None, None))
        log.info("菜单：%s %s", action, sid or "")
        if action == "open_console":
            self.window.show()
        elif action == "quit":
            self.window.quit()
        elif action == "toggle_login_launch":
            value = not settings.load()["login_launch"]
            settings.update(login_launch=value)
            settings.apply_login_launch(value)
            self.refresh_now(force=True)
        elif action == "toggle_boot_autostart":
            settings.update(boot_autostart=not settings.load()["boot_autostart"])
            self.refresh_now(force=True)
        elif action == "open_web":
            self._open_web(sid)
        elif action == "open_log":
            self._open_log(sid)
        elif action in ("start", "stop", "restart", "start_all", "stop_all"):
            self._run_in_background(action, sid)

    def _open_web(self, sid):
        err = osutil.open_web(sid)
        if err:
            self.alert("无法打开网页", err)

    def _open_log(self, sid):
        err = osutil.open_log(sid)
        if err:
            self.alert("还没有日志", err)

    # ---------- 后台线程 ----------

    def _run_in_background(self, action: str, sid: str | None, confirm: bool = False) -> None:
        def work():
            fn = getattr(actions, action)
            kwargs = {"confirm": confirm} if action != "stop_all" else {}
            r = fn(sid, **kwargs) if sid else fn(**kwargs)
            if r.needs_confirm:
                AppHelper.callAfter(self._ask_then_run, action, sid, r.prompt)
            elif not r.ok:
                AppHelper.callAfter(self.alert, "操作没有完成", r.message)
            self.refresh_now()

        threading.Thread(target=work, daemon=True).start()

    def _ask_then_run(self, action, sid, prompt):
        if self.alert("请确认", prompt, confirm=True):
            self._run_in_background(action, sid, confirm=True)
