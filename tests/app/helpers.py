"""App 测试的辅助函数：测试接口（写命令文件、读结果）、打开 App、查询进程。"""

import json
import os
import plistlib
import subprocess
import sys
import time
import uuid
from pathlib import Path

from servicedeck import paths
from servicedeck.compose import lifecycle

from ..integration.helpers import wait_for

LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"


def app_pid() -> int | None:
    try:
        return int(paths.app_lock_file().read_text().strip())
    except (OSError, ValueError):
        return None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


def send(cmd: str, timeout: float = 15) -> dict:
    cid = uuid.uuid4().hex[:8]
    out = paths.run_dir() / "test-out"
    tmp = paths.run_dir() / "test-cmd.tmp"
    tmp.write_text(f"{cid} {cmd}")
    tmp.rename(paths.run_dir() / "test-cmd")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if out.exists():
            for line in out.read_text().splitlines():
                data = json.loads(line)
                if data.get("id") == cid:
                    assert "error" not in data, data
                    return data
        time.sleep(0.1)
    log = paths.app_log_file().read_text(errors="replace")[-3000:] if paths.app_log_file().exists() else ""
    raise AssertionError(f"App 没有响应测试命令 {cmd}。App 日志：\n{log}")


MINIMAL_ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": os.path.expanduser("~"),
               "USER": os.environ.get("USER", ""), "TMPDIR": os.environ.get("TMPDIR", "/tmp")}


def open_app(bundle: Path, background: bool = True) -> None:
    """模拟从访达打开：新版 macOS 的 open 会把调用者的环境变量传给 App，所以用只有基本 PATH 的环境调用它。
    background=True 相当于登录时自动启动（窗口隐藏）；False 相当于用户双击打开（显示窗口）。"""
    cmd = ["/usr/bin/open", str(bundle)] + (["--args", "background"] if background else [])
    subprocess.run(cmd, check=True, env=MINIMAL_ENV)


def hook_quit(bundle) -> None:
    pid = app_pid()
    if pid and alive(pid):
        try:
            send("quit", timeout=5)
        except AssertionError:
            pass
        wait_for(lambda: not alive(pid), timeout=10, desc="App 退出")


def listening_tcp(pid: int) -> str:
    return subprocess.run(["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
                          capture_output=True, text=True).stdout.strip()


def rss_mb(pid: int) -> float:
    return int(subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout) / 1024


