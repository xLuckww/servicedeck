"""所有文件位置（设计 5.1）。

默认配置目录是 ~/.config/servicedeck/，可用环境变量 SERVICEDECK_HOME 覆盖。
测试和临时实例一律用覆盖后的短路径目录，保证不碰真实配置；覆盖时日志也放进该目录。
每次调用都重新读取环境变量，方便测试切换目录。
"""

import os
from pathlib import Path

# macOS 的 Unix socket 路径上限是 104 字节（含结尾的 \0）
SOCKET_PATH_MAX = 103


class SocketPathTooLong(ValueError):
    pass


def home() -> Path:
    override = os.environ.get("SERVICEDECK_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "servicedeck"


def registry_file() -> Path:
    return home() / "services.yaml"


def registry_lock_file() -> Path:
    return home() / ".services.lock"


def backups_dir() -> Path:
    return home() / "backups"


def ui_state_file() -> Path:
    return home() / "ui-state.json"


def run_dir() -> Path:
    return home() / "run"


def compose_file() -> Path:
    return run_dir() / "process-compose.yaml"


def socket_path() -> Path:
    return run_dir() / "pc.sock"


def pc_pid_file() -> Path:
    return run_dir() / "pc.pid"


def app_lock_file() -> Path:
    return run_dir() / "app.lock"


def logs_dir() -> Path:
    if os.environ.get("SERVICEDECK_HOME"):
        return home() / "logs"
    return Path.home() / "Library" / "Logs" / "ServiceDeck"


def launch_agents_dir() -> Path:
    """登录项配置所在目录；测试时（设置了 SERVICEDECK_HOME）放进临时目录，不碰真实的 LaunchAgents。"""
    if os.environ.get("SERVICEDECK_HOME"):
        return home() / "LaunchAgents"
    return Path.home() / "Library" / "LaunchAgents"


def app_log_file() -> Path:
    return logs_dir() / "servicedeck.log"


def service_log_file(service_id: str) -> Path:
    return logs_dir() / f"{service_id}.log"


def check_socket_path(path: Path) -> None:
    if len(str(path).encode()) > SOCKET_PATH_MAX:
        raise SocketPathTooLong(
            f"Unix socket 路径太长（{len(str(path).encode())} 字节，macOS 上限约 104）：{path}。"
            "请把 SERVICEDECK_HOME 换成更短的目录。"
        )


def ensure_dirs() -> None:
    for d in (home(), run_dir(), backups_dir(), logs_dir()):
        d.mkdir(parents=True, exist_ok=True)
