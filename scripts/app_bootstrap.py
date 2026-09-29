"""ServiceDeck.app 的引导文件模板（由 scripts/install_app.sh 填好路径后放进 .app 包，不要直接运行）。

为什么需要它（2026-09-28 实测）：
  .app 的启动程序若用 exec 换成另一个进程（哪怕是同一个程序文件），新版 macOS 会把这个 App 的菜单栏图标藏起来，
  系统设置 → 菜单栏里也看不到它。所以 .app 的启动程序就是 Python 本体，靠包里的 .pth 文件在启动时加载本文件，
  在同一个进程里运行 ServiceDeck。

只有"作为 App 被打开"（没有命令行参数，或只有 background）时才接管；带其他参数运行这个 Python 时不做任何事。
登录项用 `open -a ServiceDeck --args background` 启动（不能写成 --background：Python 会把它当成自己的选项而报错退出）。
"""

import os
import site
import sys

PROJECT = "__PROJECT__"
SITE_PACKAGES = "__SITE_PACKAGES__"
LOG_DIR = os.path.expanduser("__LOG_DIR__")
EXTRA_ENV = __EXTRA_ENV__

APP_FLAGS = {"background"}


def _launched_as_app() -> bool:
    args = list(getattr(sys, "orig_argv", sys.argv))[1:]
    return all(a in APP_FLAGS or a.startswith("-psn") for a in args)


def _alert(message: str) -> None:
    if os.environ.get("SERVICEDECK_NO_DIALOG"):
        return
    timeout = os.environ.get("SERVICEDECK_DIALOG_TIMEOUT", "600")
    os.spawnv(os.P_WAIT, "/usr/bin/osascript", [
        "osascript", "-e", "on run argv", "-e",
        'display alert "ServiceDeck 无法启动" message (item 1 of argv) as critical giving up after (item 2 of argv)',
        "-e", "end run", message, timeout,
    ])


def _run_app() -> None:
    if not os.path.isdir(SITE_PACKAGES):
        msg = (f"找不到 ServiceDeck 的项目文件夹或虚拟环境：{PROJECT}\n\n"
               "项目文件夹可能被移动、改名或删除了。请把它放回原处；"
               "如果是有意移动的，请在新位置运行 scripts/install_app.sh 重新安装。")
        print(msg, file=sys.stderr, flush=True)
        _alert(msg)
        os._exit(1)

    os.environ.update(EXTRA_ENV)
    os.makedirs(LOG_DIR, exist_ok=True)
    fd = os.open(os.path.join(LOG_DIR, "servicedeck.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.chdir(PROJECT)
    background = "background" in getattr(sys, "orig_argv", [])[1:]
    sys.argv = ["ServiceDeck"] + (["--background"] if background else [])

    from servicedeck.app.main import main

    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code or 0)


if os.path.isdir(SITE_PACKAGES):
    site.addsitedir(SITE_PACKAGES)
if _launched_as_app():
    _run_app()
