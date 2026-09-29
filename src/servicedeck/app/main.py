"""ServiceDeck.app 入口：单实例锁 → 隐藏窗口 + 菜单栏图标 → 接上或启动服务管理器 → 每 3 秒刷新状态。

运行：python -m servicedeck.app.main（正式使用时由 /Applications/ServiceDeck.app 的启动脚本调用）。
"""

import fcntl
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import webview
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
from PyObjCTools import AppHelper

from servicedeck import paths, registry, status
from servicedeck.app import settings
from servicedeck.app.bridge import Bridge
from servicedeck.app.menubar import MenuBar
from servicedeck.app.window import TITLE, WindowController
from servicedeck.compose import lifecycle
from servicedeck.compose.client import ComposeClient

REFRESH_SECONDS = 3
WEB_DIR = Path(__file__).parent / "web"
log = logging.getLogger("servicedeck.app")


def adopt_bundle_identity() -> None:
    """App 实际运行的是 .venv 里的 python，程序内部的"主程序包"是 python 而不是 ServiceDeck.app。
    这里补上 ServiceDeck 的标识和名字（设置、菜单栏图标的记忆都按它归属）。必须在创建 NSApplication 之前调用。"""
    from Foundation import NSBundle

    info = NSBundle.mainBundle().infoDictionary()
    if info is not None and not info.get("CFBundleIdentifier"):
        info["CFBundleIdentifier"] = os.environ.get("SERVICEDECK_BUNDLE_ID", "local.servicedeck")
        info["CFBundleName"] = "ServiceDeck"


def allow_quit_requests(window_ctrl: WindowController) -> None:
    """⌘Q、程序坞右键"退出"、关机/注销都会走 applicationShouldTerminate：这些一律真正退出。
    pywebview 在这里会询问窗口能否关闭，而我们的"关闭窗口 = 隐藏"会拒绝，所以先标记为正在退出。
    红色关闭按钮走的是 windowShouldClose，仍然只隐藏窗口。"""
    from webview.platforms import cocoa

    delegate_cls = cocoa.BrowserView.AppDelegate
    original = delegate_cls.applicationShouldTerminate_

    def applicationShouldTerminate_(self, app):
        window_ctrl.quitting = True
        log.info("收到退出请求（⌘Q / 程序坞 / 关机），退出 ServiceDeck，服务继续运行")
        return original(self, app)

    delegate_cls.applicationShouldTerminate_ = applicationShouldTerminate_


def load_page() -> str:
    """把 app.css、app.js 内嵌进 index.html（源文件分开维护，便于修改）。"""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "app.css").read_text(encoding="utf-8")
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="app.css">', f"<style>\n{css}\n</style>")
    return html.replace('<script src="app.js"></script>', f"<script>\n{js}\n</script>")


def acquire_lock():
    """对 run/app.lock 加文件锁，防止绕过 macOS 直接运行启动脚本造成两个实例。拿不到返回 None。"""
    paths.ensure_dirs()
    fh = open(paths.app_lock_file(), "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


class Refresher:
    """后台线程：每 3 秒查询一次状态，只在菜单内容变化时回主线程更新图标和菜单。"""

    def __init__(self, menubar: MenuBar):
        self.menubar = menubar
        self.wake = threading.Event()
        self.force = False
        self.last_key = None

    def trigger(self, force: bool = False) -> None:
        self.force = self.force or force
        self.wake.set()

    def loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("刷新状态出错")
            self.wake.wait(REFRESH_SECONDS)
            self.wake.clear()

    def tick(self) -> None:
        try:
            summary, error = status.collect(), None
        except registry.RegistryError as e:
            summary, error = None, str(e)
        key = json.dumps([_menu_key(summary), error, settings.load()], ensure_ascii=False, default=str)
        if key != self.last_key or self.force:
            self.last_key, self.force = key, False
            AppHelper.callAfter(self.menubar.render, summary, error)


def _menu_key(summary):
    if summary is None:
        return None
    return [summary.overall, summary.manager_running, summary.running_count, summary.error_count,
            [(g.id, g.name, g.running, g.total) for g in summary.groups],
            [(s.id, s.name, s.display, s.port) for s in summary.services]]


def start_services() -> None:
    """接上正在运行的服务管理器；未运行时按"开机自动启动服务"的设置决定是否全部启动（设计 8）。"""
    alive = ComposeClient().is_alive()
    boot = settings.boot_time()
    if settings.should_autostart(alive, boot):
        log.info("本次开机第一次打开，自动启动全部服务")
        settings.mark_boot_handled(boot)
        try:
            lifecycle.ensure_running()
        except Exception:
            log.exception("自动启动服务失败")
    elif alive:
        log.info("已接上正在运行的服务管理器")
        settings.mark_boot_handled(boot)


class TestHook:
    """仅在环境变量 SERVICEDECK_TEST_HOOK=1 时启用：自动测试往 run/test-cmd 写命令，结果追加到 run/test-out。"""

    def __init__(self, window: WindowController, menubar: MenuBar):
        self.window, self.menubar = window, menubar
        self.cmd_file = paths.run_dir() / "test-cmd"
        self.out_file = paths.run_dir() / "test-out"

    def loop(self) -> None:
        while True:
            if self.cmd_file.exists():
                text = self.cmd_file.read_text().strip()
                self.cmd_file.unlink()
                if text:
                    cid, _, cmd = text.partition(" ")
                    self._run(cid, cmd)
            time.sleep(0.1)

    def _run(self, cid: str, cmd: str) -> None:
        if cmd.startswith("js "):
            # evaluate_js 会等主线程返回结果，必须在后台线程调用
            try:
                result = {"value": self.window.window.evaluate_js(cmd[3:])}
            except Exception as e:
                result = {"error": repr(e)}
            with open(self.out_file, "a") as f:
                f.write(json.dumps({"id": cid, **result}, ensure_ascii=False, default=str) + "\n")
            return
        done = threading.Event()
        result = {}

        def on_main():
            try:
                result.update(self._execute(cmd))
            except Exception as e:  # 测试需要看到原因
                result["error"] = repr(e)
            done.set()

        AppHelper.callAfter(on_main)
        done.wait(10)
        with open(self.out_file, "a") as f:
            f.write(json.dumps({"id": cid, **result}, ensure_ascii=False) + "\n")

    def _execute(self, cmd: str) -> dict:
        app = NSApplication.sharedApplication()
        if cmd == "open_console":
            self.window.show()
        elif cmd == "close_window":
            self.window.perform_close()
        elif cmd == "quit":
            AppHelper.callLater(0.3, self.window.quit)
        elif cmd == "terminate":
            # 模拟 Cmd+Q / 其他程序请求退出（未关机时应被当作"关闭窗口"而不退出）
            AppHelper.callLater(0.3, app.terminate_, None)
        elif cmd == "deactivate":
            from AppKit import NSRunningApplication
            finder = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.finder")
            if finder:
                finder[0].activateWithOptions_(0)
        elif cmd == "reactivate":
            # 与 ⌘Tab 一样：由系统激活本程序
            from AppKit import NSRunningApplication
            NSRunningApplication.currentApplication().activateWithOptions_(0)
        elif cmd == "simulate_logout":
            from AppKit import NSWorkspace
            NSWorkspace.sharedWorkspace().notificationCenter().postNotificationName_object_(
                "NSWorkspaceWillPowerOffNotification", None)
            AppHelper.callLater(0.3, app.terminate_, None)
        elif cmd.startswith("appearance "):
            from AppKit import NSAppearance
            name = {"dark": "NSAppearanceNameDarkAqua", "light": "NSAppearanceNameAqua"}.get(cmd.split()[1])
            app.setAppearance_(NSAppearance.appearanceNamed_(name) if name else None)
        elif cmd.startswith("snapshot "):
            self._snapshot(cmd.split(" ", 1)[1])
        elif cmd.startswith("menu "):
            # 模拟点击菜单项：menu <动作> [服务 id]
            parts = cmd.split()
            wanted = (parts[1], parts[2] if len(parts) > 2 else None)
            tag = next((t for t, a in self.menubar.actions_by_tag.items() if a == wanted), None)
            if tag is None:
                return {"error": f"菜单里没有 {wanted}"}
            self.menubar.handle(tag)
        return {
            "pid": os.getpid(),
            "visible": self.window.is_visible(),
            "policy": int(app.activationPolicy()),  # 0 = 常规（Dock 可见），1 = 仅菜单栏
            "active": bool(app.isActive()),
            "icon": self.menubar.last_icon[0],
            "menu": self.menubar.last_titles,
            "key_window": self.window.is_key(),
            "bundle_id": str(__import__("Foundation").NSBundle.mainBundle().bundleIdentifier()),
            "defaults_has_position": __import__("Foundation").NSUserDefaults.standardUserDefaults().objectForKey_(
                "NSStatusItem Preferred Position ServiceDeck") is not None,
            "windows": [[str(w.className()), str(w.title()), bool(w.isVisible())] for w in app.windows()],
            "status_item": self.menubar.status_item_info(),
        }


    def _snapshot(self, path: str) -> None:
        """把窗口网页内容截成 PNG（WKWebView 自带的截图，不需要屏幕录制权限）。"""
        from AppKit import NSBitmapImageRep, NSPNGFileType
        from webview.platforms.cocoa import BrowserView

        wk = list(BrowserView.instances.values())[0].webview

        def done(image, error):
            if image is not None:
                rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                rep.representationUsingType_properties_(NSPNGFileType, None).writeToFile_atomically_(path, True)

        wk.takeSnapshotWithConfiguration_completionHandler_(None, done)


def on_started(window_ctrl: WindowController) -> None:
    """pywebview 启动后在后台线程调用。"""
    menubar = MenuBar(window_ctrl, refresh_now=lambda force=False: refresher.trigger(force))
    refresher = Refresher(menubar)
    AppHelper.callAfter(menubar.setup)
    AppHelper.callLater(3, menubar.log_status_item)
    AppHelper.callLater(2, setattr, window_ctrl, "started", True)
    if "--background" not in sys.argv:
        # 用户自己打开 App：直接显示窗口；登录时由登录项带 --background 启动，只留菜单栏图标
        AppHelper.callLater(1.5, window_ctrl.show)
    # pywebview 启动时会把 App 设为"常规"模式（出现在 Dock），这里改回"仅菜单栏"（原型踩过的坑）
    AppHelper.callAfter(NSApplication.sharedApplication().setActivationPolicy_, NSApplicationActivationPolicyAccessory)
    try:
        settings.apply_login_launch(settings.load()["login_launch"])
    except OSError:
        log.exception("写入登录项失败")
    threading.Thread(target=refresher.loop, daemon=True, name="refresh").start()
    if settings.is_test_mode():
        threading.Thread(target=TestHook(window_ctrl, menubar).loop, daemon=True, name="test-hook").start()
    start_services()
    refresher.trigger()


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # 每 3 秒一次的状态查询不写进日志
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    lock = acquire_lock()
    if lock is None:
        print("ServiceDeck 已在运行（只允许一个实例）", file=sys.stderr)
        return 0
    log.info("ServiceDeck 启动，PID %s，配置目录 %s", os.getpid(), paths.home())

    adopt_bundle_identity()
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    # 页面以 HTML 文本直接交给窗口：若传本地文件路径，pywebview 会在 127.0.0.1 开一个 HTTP 端口（设计要求不开放任何端口）
    api = Bridge()
    window = webview.create_window(TITLE, html=load_page(), js_api=api, width=900, height=640,
                                   min_size=(700, 440), hidden=True, text_select=True)
    api._set_window(window)
    window_ctrl = WindowController(window)
    window.events.closing += window_ctrl.on_closing
    allow_quit_requests(window_ctrl)
    webview.start(on_started, (window_ctrl,), private_mode=True)
    lock.close()
    log.info("ServiceDeck 已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
