"""设置与界面状态（ui-state.json）：登录时启动、开机自动启动服务、分组折叠等（设计 8）。"""

import json
import os
import plistlib
import re
import subprocess

from servicedeck import paths

LABEL = "local.servicedeck"
DEFAULTS = {"login_launch": True, "boot_autostart": True}


def load() -> dict:
    try:
        data = json.loads(paths.ui_state_file().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    return {**DEFAULTS, **data}


def update(**changes) -> dict:
    from servicedeck import registry

    data = {**load(), **changes}
    paths.ensure_dirs()
    registry._atomic_write(paths.ui_state_file(), json.dumps(data, ensure_ascii=False, indent=2))
    return data


# ---------- 登录时启动 ----------

def launch_agent_file():
    return paths.launch_agents_dir() / f"{LABEL}.plist"


def apply_login_launch(enabled: bool) -> None:
    """打开时写入登录后打开 ServiceDeck.app 的配置，关闭时删除。
    不用"系统设置 → 登录项"的接口，因为它要求 App 有开发者签名。"""
    f = launch_agent_file()
    if not enabled:
        f.unlink(missing_ok=True)
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "Label": LABEL,
        # background：登录时只在菜单栏出现，不弹出窗口（见 scripts/app_bootstrap.py）
        "ProgramArguments": ["/usr/bin/open", "-a", "ServiceDeck", "--args", "background"],
        "RunAtLoad": True,
    }
    content = plistlib.dumps(data)
    if not f.exists() or f.read_bytes() != content:
        f.write_bytes(content)


# ---------- 开机自动启动服务 ----------

def parse_boottime(text: str) -> int | None:
    m = re.search(r"sec\s*=\s*(\d+)", text or "")
    return int(m.group(1)) if m else None


def boot_time() -> int:
    out = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout
    return parse_boottime(out) or 0


def should_autostart(manager_alive: bool, boot: int) -> bool:
    """服务管理器未运行、设置打开、且本次开机还没自动启动过，才自动"全部启动"。
    这样手动"全部停止"后再重开 App，服务不会被自动拉起。"""
    s = load()
    return bool(s["boot_autostart"]) and not manager_alive and s.get("autostart_boot") != boot


def mark_boot_handled(boot: int) -> None:
    update(autostart_boot=boot)


def is_test_mode() -> bool:
    return bool(os.environ.get("SERVICEDECK_TEST_HOOK"))
