"""控制台窗口的显示/隐藏与 Dock 模式切换（设计 6.4，参照 2026-09-28 原型）。

- 打开：恢复最小化 → 显示 → 切到"常规"模式（出现在 Dock 和 Cmd+Tab）→ 提到最前；
- 关闭（红色按钮或 Cmd+W）：只隐藏窗口，切回"仅菜单栏"模式；
- 退出时不拦截关闭（原型踩过的坑：否则 App 退不掉）。
所有方法都要在主线程调用（用 AppHelper.callAfter）。
"""

import logging

from AppKit import NSApplication, NSApplicationActivationPolicyAccessory, NSApplicationActivationPolicyRegular
from PyObjCTools import AppHelper

TITLE = "ServiceDeck"
log = logging.getLogger(__name__)


class WindowController:
    def __init__(self, window):
        self.window = window
        self.quitting = False
        self.suppress_activation = 0  # 弹确认框等自己激活 App 的时候，不要顺带弹出窗口
        self.started = False  # 启动时系统发来的"被激活"不算"再次打开 App"（设计：启动后窗口隐藏）

    def _ns_windows(self):
        # 只认控制台窗口：菜单栏图标自己的窗口（NSStatusBarWindow）标题也叫 ServiceDeck
        return [w for w in NSApplication.sharedApplication().windows()
                if w.title() == TITLE and not str(w.className()).startswith("NSStatusBar")]

    def is_visible(self) -> bool:
        return any(w.isVisible() for w in self._ns_windows())

    def show(self) -> None:
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        self.window.restore()
        self.window.show()
        app.activateIgnoringOtherApps_(True)
        # 新版 macOS 不一定允许程序自己抢到前台（没有用户点击时），至少保证窗口排在最前、不被挡住
        for w in self._ns_windows():
            w.orderFrontRegardless()
        log.info("显示控制台窗口")

    def hide(self) -> None:
        self.window.hide()
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        log.info("隐藏控制台窗口")

    def perform_close(self) -> None:
        """模拟点击红色关闭按钮（测试用）。"""
        for w in self._ns_windows():
            w.performClose_(None)

    def on_closing(self):
        if self.quitting:
            return True
        AppHelper.callAfter(self.hide)
        return False

    def on_app_activated(self) -> None:
        # 在访达、Dock、启动台里再次打开 App（或 open 命令）时，macOS 只会激活已有实例
        if self.suppress_activation or self.quitting or not self.started:
            return
        if not self.is_visible():
            log.info("收到激活事件（再次打开 App）→ 显示窗口")
            self.show()
        else:
            # ⌘Tab 切换过来时，从"仅菜单栏"切成"常规"模式的 App 不会自动把窗口提到最前，这里主动提
            self.bring_front()

    def bring_front(self) -> None:
        for w in self._ns_windows():
            if w.isMiniaturized():
                w.deminiaturize_(None)
            w.makeKeyAndOrderFront_(None)
            w.orderFrontRegardless()

    def is_key(self) -> bool:
        return any(w.isKeyWindow() for w in self._ns_windows())

    def quit(self) -> None:
        self.quitting = True
        log.info("退出 ServiceDeck")
        NSApplication.sharedApplication().terminate_(None)
