"""打开网页、用"控制台"App 打开日志、在访达中显示（菜单栏和窗口共用）。"""

import subprocess
from pathlib import Path

from servicedeck import paths, registry


def open_web(service_id: str) -> str | None:
    s = registry.load().service(service_id)
    if not s or not s.port:
        return "这个服务没有设置端口"
    subprocess.Popen(["/usr/bin/open", f"http://127.0.0.1:{s.port}"])
    return None


def open_log(service_id: str) -> str | None:
    f = paths.service_log_file(service_id)
    if not f.exists():
        return f"服务还没有产生日志：{f}"
    subprocess.Popen(["/usr/bin/open", "-a", "Console", str(f)])
    return None


def reveal(path: str) -> str | None:
    p = Path(path).expanduser()
    if not p.exists():
        return f"不存在：{path}"
    subprocess.Popen(["/usr/bin/open", "-R", str(p)])
    return None
