"""服务的运行环境（设计 5.4）。

从访达、Dock 或登录项打开的 App 只有最基本的 PATH，找不到 uv、mise、npm、node。
因此每个服务（以及 process-compose 本身）都注入统一的 PATH；模板里用到的工具解析成绝对路径。
排在最前面的 mise shims 会按服务的工作文件夹自动选版本。
"""

import os

SERVICE_PATH_DIRS = [
    "~/.local/share/mise/shims",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "~/.local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]


class ToolNotFound(Exception):
    pass


def service_path_dirs() -> list[str]:
    return [os.path.expanduser(d) for d in SERVICE_PATH_DIRS]


def service_path() -> str:
    return ":".join(service_path_dirs())


def service_env() -> dict[str, str]:
    """启动 process-compose 用的最小环境：统一 PATH，外加 HOME 等基本变量。"""
    env = {"PATH": service_path(), "HOME": os.path.expanduser("~")}
    for key in ("USER", "LOGNAME", "LANG", "TMPDIR", "SHELL"):
        if key in os.environ:
            env[key] = os.environ[key]
    env.setdefault("LANG", "en_US.UTF-8")
    return env


def resolve_tool(name: str) -> str:
    """在服务 PATH 中查找工具的绝对路径（不看当前进程的 PATH）。"""
    for d in service_path_dirs():
        candidate = os.path.join(d, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise ToolNotFound(f"找不到 {name}：在 {service_path()} 中都没有找到，请先安装它")
