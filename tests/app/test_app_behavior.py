"""App 行为测试（移植自 2026-09-28 原型）：在临时目录生成测试用的 .app，用 open 打开，通过测试接口读取窗口状态。

会在菜单栏出现 SD 图标、弹出窗口。默认跳过，运行方式：SERVICEDECK_APP_TESTS=1 uv run pytest tests/app
"""

import os
import subprocess
import time

import pytest

from servicedeck import paths, registry
from servicedeck.app import settings
from servicedeck.compose import lifecycle
from servicedeck.compose.client import ComposeClient

from ..integration.helpers import http_service, make_registry, port_open, wait_for
from .helpers import alive, app_pid, listening_tcp, open_app, rss_mb, send

pytestmark = pytest.mark.app

def test_window_lifecycle_and_single_instance(test_app):
    registry.save(make_registry(http_service("alpha", 18392)))
    open_app(test_app)
    r = wait_for(lambda: send("report"), timeout=30, desc="App 启动")
    pid = r["pid"]
    time.sleep(1.5)

    # 启动后：无 Dock 图标（仅菜单栏模式）、窗口隐藏
    r = send("report")
    assert r["visible"] is False and r["policy"] == 1, r
    assert listening_tcp(pid) == "", "App 不应监听任何网络端口"
    # 经 open（与访达相同，由 LaunchServices 启动）打开时只有最基本的 PATH，找不到 uv、mise、npm
    env = subprocess.run(["ps", "eww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    app_path = [x for x in env.split() if x.startswith("PATH=")]
    assert app_path == ["PATH=/usr/bin:/bin:/usr/sbin:/sbin"], f"App 进程的 PATH 不是最小环境：{app_path}"

    # 开机自动启动：服务管理器被启动，菜单栏变绿
    r = wait_for(lambda: (x := send("report"))["icon"] == "green" and x, timeout=30, desc="菜单栏变绿")
    assert "打开控制台" in r["menu"] and any("alpha" in t for t in r["menu"])
    assert port_open(18392)

    # 打开控制台：窗口可见、出现在 Dock、在前台
    r = send("open_console")
    time.sleep(1.5)
    r = send("report")
    assert r["visible"] is True and r["policy"] == 0, r
    # 没有真实点击时，新版 macOS 可能拒绝程序自己抢前台（真实使用由点击触发，没有这个问题），只记录不判失败
    print(f"\n打开控制台后在前台：{r['active']}")

    # 点红色关闭按钮：只隐藏，回到仅菜单栏模式，进程仍在
    send("close_window")
    time.sleep(1.5)
    r = send("report")
    assert r["visible"] is False and r["policy"] == 1, r
    assert alive(pid)

    # 再次打开 App：窗口恢复
    open_app(test_app)
    wait_for(lambda: send("report")["visible"], timeout=10, desc="再次打开后窗口可见")
    assert app_pid() == pid

    # 绕过 macOS 直接运行 App 的程序：被单实例锁挡住
    launcher = test_app / "Contents" / "MacOS" / "ServiceDeck"
    p = subprocess.run([str(launcher)], timeout=30, env={"PATH": "/usr/bin:/bin", "HOME": os.path.expanduser("~")})
    assert p.returncode == 0
    assert app_pid() == pid and alive(pid)

    mem = rss_mb(pid)
    print(f"\n常驻内存（主进程 RSS）：{mem:.0f} MB")
    (paths.home() / "rss.txt").write_text(f"{mem:.0f}")
    assert mem < 150

    # 退出：进程结束，服务照常运行
    pc_pid = lifecycle.read_pid()
    send("quit")
    wait_for(lambda: not alive(pid), timeout=10, desc="退出后进程结束")
    assert port_open(18392) and ComposeClient().is_alive()

    # 重开 App：接上正在运行的服务管理器，不重复启动
    open_app(test_app)
    wait_for(lambda: send("report")["icon"] == "green", timeout=30, desc="重开后接上")
    assert lifecycle.read_pid() == pc_pid


def test_no_autostart_after_stop_all_in_same_boot(test_app):
    registry.save(make_registry(http_service("alpha", 18393)))
    settings.mark_boot_handled(settings.boot_time())  # 本次开机已经自动启动过
    open_app(test_app)
    wait_for(lambda: send("report"), timeout=30, desc="App 启动")
    time.sleep(3)
    r = send("report")
    assert r["icon"] == "grey" and "启动全部服务" in r["menu"], r
    assert not ComposeClient().is_alive()


def test_menu_actions_start_stop(test_app):
    registry.save(make_registry(http_service("alpha", 18394)))
    open_app(test_app)
    wait_for(lambda: send("report")["icon"] == "green", timeout=30, desc="App 启动并变绿")
    send("menu stop alpha")
    wait_for(lambda: not port_open(18394), desc="菜单停止服务")
    wait_for(lambda: send("report")["icon"] == "grey", desc="全部停着时图标变灰")
    send("menu start alpha")
    wait_for(lambda: send("report")["icon"] == "green", timeout=20, desc="菜单启动服务")
    assert port_open(18394)
    send("menu stop_all")
    wait_for(lambda: "启动全部服务" in send("report")["menu"], timeout=20, desc="全部停止后菜单只剩启动全部服务")
    assert not ComposeClient().is_alive()
    send("menu start_all")
    wait_for(lambda: send("report")["icon"] == "green", timeout=30, desc="启动全部服务")


def test_quit_requests_really_quit(test_app):
    """⌘Q、Dock 右键"退出"（都会走系统的退出请求）真正退出 App；红色关闭按钮只隐藏窗口。"""
    registry.save(make_registry())
    open_app(test_app)
    pid = wait_for(lambda: send("report"), timeout=30, desc="App 启动")["pid"]
    send("open_console")
    time.sleep(1)
    send("close_window")
    time.sleep(1.5)
    assert alive(pid) and send("report")["visible"] is False
    send("terminate")
    wait_for(lambda: not alive(pid), timeout=10, desc="⌘Q 后 App 退出")


def test_logout_or_shutdown_is_not_blocked(test_app):
    registry.save(make_registry())
    open_app(test_app)
    pid = wait_for(lambda: send("report"), timeout=30, desc="App 启动")["pid"]
    send("simulate_logout")
    wait_for(lambda: not alive(pid), timeout=10, desc="注销时 App 退出")


def test_reactivation_brings_window_front_and_status_item_exists(test_app):
    registry.save(make_registry())
    open_app(test_app)
    wait_for(lambda: send("report"), timeout=30, desc="App 启动")
    r = send("report")
    assert r["status_item"]["visible"] is True and r["status_item"]["autosave"] == "ServiceDeck", r
    # 系统认定的身份是 ServiceDeck（而不是 python），菜单栏图标的偏好也记在它名下
    assert r["bundle_id"] == "local.servicedeck.test", r
    assert r["defaults_has_position"] is True
    send("open_console")
    time.sleep(1)
    # 模拟 ⌘Tab：从外部先让访达到前台，再由系统激活 ServiceDeck
    subprocess.run(["osascript", "-e", 'tell application "Finder" to activate'], check=True)
    time.sleep(1.5)
    assert send("report")["active"] is False
    subprocess.run(["osascript", "-e", 'tell application id "local.servicedeck.test" to activate'], check=True)
    r = wait_for(lambda: (x := send("report"))["key_window"] and x, timeout=10, desc="⌘Tab 后窗口在最前")
    assert r["visible"] is True and r["active"] is True


def test_manual_open_shows_window_and_menubar_icon_is_visible(test_app):
    """用户双击打开 App：直接显示窗口；菜单栏图标真正显示在菜单栏上（不是被系统藏起来）。"""
    registry.save(make_registry())
    open_app(test_app, background=False)
    r = wait_for(lambda: (x := send("report"))["visible"] and x, timeout=30, desc="手动打开后窗口显示")
    assert r["policy"] == 0
    item = wait_for(lambda: (x := send("report")["status_item"])["x"] > 0 and x, timeout=10, desc="菜单栏图标有位置")
    assert item["occlusion_visible"] is True, item
