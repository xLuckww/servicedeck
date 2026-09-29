"""服务清单 → process-compose 配置（设计 5.3）。

注意（2026-09-28 实测 v1.122.0）：process-compose 加载配置时会自己展开 ${VAR} 和 Go 模板 {{ }}，
所以命令、环境变量值、检查命令里的 $ 要写成 $$、{{ 要写成 {{"{{"}}，才能原样交给 bash。
"""

import math
import os
import re

import yaml

from servicedeck import envpath, paths, registry
from servicedeck.models import Registry, Service

MAX_RESTARTS = 3
SHUTDOWN_TIMEOUT = 30  # 秒；超时后强制结束，给数据库等留足落盘时间
PORT_PROBE = {"initial_delay": 1, "period": 2, "timeout": 60}

HEADER = "# 由 ServiceDeck 根据 services.yaml 自动生成，不要手改（改了也会被覆盖）\n"


def escape(text: str) -> str:
    return text.replace("$", "$$").replace("{{", '{{"{{"}}')


def _expand_tilde_words(command: str) -> str:
    home = os.path.expanduser("~")
    return re.sub(r"(^|\s)~(?=/|\s|$)", lambda m: m.group(1) + home, command)


def _abs(path: str, base: str | None = None) -> str:
    p = os.path.expanduser(path)
    if not os.path.isabs(p) and base:
        p = os.path.join(os.path.expanduser(base), p)
    return p


def has_probe(s: Service) -> bool:
    return s.health is not None or s.port is not None


def _probe(s: Service) -> dict | None:
    if s.health is None and s.port is None:
        return None
    if s.health is None:
        initial, period, timeout = PORT_PROBE["initial_delay"], PORT_PROBE["period"], PORT_PROBE["timeout"]
        check = {"exec": {"command": f"/usr/bin/nc -z 127.0.0.1 {s.port}"}}
    else:
        h = s.health
        initial, period, timeout = h.initial_delay, max(1, h.period), h.timeout
        if h.type == "http":
            check = {"http_get": {"host": "127.0.0.1", "port": s.port, "path": h.path, "scheme": "http"}}
        else:
            check = {"exec": {"command": escape(h.command)}}
    return {
        **check,
        "initial_delay_seconds": initial,
        "period_seconds": period,
        "timeout_seconds": min(period, 5),
        "failure_threshold": max(1, math.ceil(timeout / period)),
    }


def _process(s: Service, reg: Registry) -> dict:
    env = {"PATH": envpath.service_path(), **s.env}
    # 不写 description（显示名）：改名不应让 process-compose 认为配置变了而重启服务
    p: dict = {
        "command": escape(_expand_tilde_words(s.command)),
        "environment": [f"{k}={escape(v)}" for k, v in env.items()],
        "disable_dotenv": True,
        "disabled": not s.autostart,
        "log_location": str(paths.service_log_file(s.id)),
        "log_configuration": {
            "rotation": {"max_size_mb": 10, "max_backups": 3},
            "disable_json": True,
            "add_timestamp": True,
            "timestamp_format": "2006-01-02 15:04:05",
            "no_color": True,
            "no_metadata": True,
            "fields_order": ["time", "message"],
            "flush_each_line": True,
        },
        "availability": {"restart": s.restart} if s.restart == "no"
        else {"restart": s.restart, "max_restarts": MAX_RESTARTS},
        "shutdown": {"signal": s.shutdown_signal, "timeout_seconds": SHUTDOWN_TIMEOUT},
    }
    if s.cwd:
        p["working_dir"] = _abs(s.cwd)
    if s.env_file:
        # 只引用文件，不把里面的值写进生成的配置
        p["env_file"] = _abs(s.env_file, s.cwd)
    probe = _probe(s)
    if probe:
        p["readiness_probe"] = probe
    if s.depends_on:
        deps = {}
        for dep_id in s.depends_on:
            dep = reg.service(dep_id)
            cond = "process_healthy" if dep is not None and has_probe(dep) else "process_started"
            deps[dep_id] = {"condition": cond}
        p["depends_on"] = deps
    return p


def to_compose(reg: Registry) -> dict:
    return {
        "version": "0.5",
        "log_location": str(paths.logs_dir() / "process-compose.log"),
        "log_level": "info",
        "processes": {s.id: _process(s, reg) for s in reg.services},
    }


def render(reg: Registry) -> str:
    return HEADER + yaml.safe_dump(to_compose(reg), allow_unicode=True, sort_keys=False, width=1000)


def write(reg: Registry) -> None:
    paths.ensure_dirs()
    registry._atomic_write(paths.compose_file(), render(reg))
