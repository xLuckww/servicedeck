"""状态汇总：每个服务的显示状态、每组的"运行数/总数"、整体颜色（设计 6.1、8）。菜单栏、窗口、命令行共用。

判定规则来自 2026-09-28 对 process-compose v1.122.0 的实测：
- 用户停止的服务：Completed，退出码 -1（被信号结束）或 0；
- 崩溃并用完重启次数：Completed，退出码非 0；依赖没起来的服务：Skipped。
"""

import json
from dataclasses import asdict, dataclass, field

from servicedeck import paths

from servicedeck.compose.config import MAX_RESTARTS
from servicedeck.models import Registry, Service

# display → (颜色, 中文说明)
DISPLAY = {
    "healthy": ("green", "运行中"),
    "starting": ("yellow", "启动中"),
    "stopping": ("yellow", "停止中"),
    "stopped": ("grey", "已停止"),
    "manual": ("grey", "手动开启"),
    "error": ("red", "异常"),
}

# 被信号结束或正常退出：0、-1（信号）、130（SIGINT）、137（SIGKILL，停止超时被强制结束）、143（SIGTERM）
NORMAL_EXIT_CODES = {0, -1, 130, 137, 143}
STARTING_STATES = {"Pending", "Launching", "Launched", "Restarting", "Foreground", "Scheduled"}


@dataclass
class ServiceStatus:
    id: str
    name: str
    group: str
    display: str
    color: str
    label: str
    manual: bool  # autostart: false（手动开启的服务）
    port: int | None = None
    pid: int | None = None
    uptime_seconds: int | None = None
    restarts: int = 0
    exit_code: int | None = None
    reason: str | None = None  # 异常原因
    raw_status: str | None = None

    @property
    def counted(self) -> bool:
        """是否计入"运行数/总数"和整体状态：停着的手动开启服务不计。"""
        return self.display != "manual"

    @property
    def running(self) -> bool:
        return self.display in ("healthy", "starting", "stopping") and self.pid is not None


@dataclass
class GroupStatus:
    id: str
    name: str
    running: int
    total: int
    services: list[ServiceStatus] = field(default_factory=list)


@dataclass
class Summary:
    manager_running: bool
    overall: str  # green / yellow / red / grey
    running_count: int
    error_count: int
    groups: list[GroupStatus]
    services: list[ServiceStatus]

    def group(self, group_id: str) -> GroupStatus | None:
        return next((g for g in self.groups if g.id == group_id), None)

    def service(self, service_id: str) -> ServiceStatus | None:
        return next((s for s in self.services if s.id == service_id), None)

    def to_dict(self) -> dict:
        def svc(s: ServiceStatus) -> dict:
            return {**asdict(s), "counted": s.counted, "running": s.running}

        return {
            "manager_running": self.manager_running,
            "overall": self.overall,
            "running_count": self.running_count,
            "error_count": self.error_count,
            "groups": [{"id": g.id, "name": g.name, "running": g.running, "total": g.total,
                        "services": [svc(s) for s in g.services]} for g in self.groups],
            "services": [svc(s) for s in self.services],
        }


def _classify(proc: dict | None) -> tuple[str, str | None]:
    if proc is None:
        return "stopped", None
    st = proc.get("status")
    exit_code = proc.get("exit_code")
    restarts = proc.get("restarts") or 0
    if st == "Disabled":
        return "stopped", None
    if st == "Running":
        if proc.get("has_ready_probe") and proc.get("is_ready") != "Ready":
            return "starting", None
        return "healthy", None
    if st in STARTING_STATES:
        return "starting", None
    if st == "Terminating":
        return "stopping", None
    if st == "Skipped":
        return "error", "依赖的服务没有启动成功，所以没有启动"
    if st == "Completed":
        if exit_code in NORMAL_EXIT_CODES and restarts < MAX_RESTARTS:
            return "stopped", None
        if restarts:
            return "error", f"程序退出（退出码 {exit_code}），已自动重启 {restarts} 次仍失败"
        return "error", f"程序退出（退出码 {exit_code}）"
    if st == "Error":
        return "error", "启动失败"
    return "starting", None


def _user_stopped_file():
    return paths.run_dir() / "user-stopped.json"


def load_user_stopped() -> set[str]:
    """用户在 ServiceDeck 里手动停止的服务。光看退出码和重启次数分不清"手动停止"和"崩溃"
    （例如之前失败重启过 3 次、后来启动成功的服务，process-compose 不会清零重启次数），所以自己记一份。"""
    try:
        return set(json.loads(_user_stopped_file().read_text()))
    except (OSError, ValueError, TypeError):
        return set()


def _save_user_stopped(ids: set[str]) -> None:
    paths.run_dir().mkdir(parents=True, exist_ok=True)
    _user_stopped_file().write_text(json.dumps(sorted(ids)))


def mark_user_stopped(ids) -> None:
    _save_user_stopped(load_user_stopped() | set(ids))


def clear_user_stopped(ids=None) -> None:
    """ids 为空表示全部清除。"""
    _save_user_stopped(set() if ids is None else load_user_stopped() - set(ids))


def service_status(s: Service, proc: dict | None, user_stopped: bool = False) -> ServiceStatus:
    display, reason = _classify(proc)
    if display == "error" and user_stopped and (proc or {}).get("status") == "Completed":
        display, reason = "stopped", None  # 用户手动停止的，不算异常
    manual = not s.autostart
    if manual and display == "stopped":
        display = "manual"
    color, label = DISPLAY[display]
    running = proc is not None and bool(proc.get("is_running")) and display != "stopped"
    age = (proc or {}).get("age") or 0
    return ServiceStatus(
        id=s.id, name=s.name, group=s.group, display=display, color=color, label=label, manual=manual,
        port=s.port,
        pid=(proc or {}).get("pid") or None if running else None,
        uptime_seconds=int(age // 1_000_000_000) if running and age else None,
        restarts=(proc or {}).get("restarts") or 0,
        exit_code=(proc or {}).get("exit_code") if proc else None,
        reason=reason,
        raw_status=(proc or {}).get("status"),
    )


def summarize(reg: Registry, processes: list[dict] | None, user_stopped: set[str] | None = None) -> Summary:
    """processes 为 None 表示服务管理器未运行；user_stopped 为用户手动停止的服务。"""
    by_name = {p["name"]: p for p in (processes or [])}
    stopped = user_stopped or set()
    services = [service_status(s, by_name.get(s.id), s.id in stopped) for s in reg.services]
    counted = [s for s in services if s.counted]

    groups = []
    for g in reg.groups:
        members = [s for s in services if s.group == g.id]
        groups.append(GroupStatus(
            id=g.id, name=g.name,
            running=sum(1 for s in members if s.counted and s.running),
            total=sum(1 for s in members if s.counted),
            services=members,
        ))

    error_count = sum(1 for s in counted if s.display == "error")
    running_count = sum(1 for s in counted if s.running)
    if processes is None:
        overall = "grey"
    elif error_count:
        overall = "red"
    elif any(s.display in ("starting", "stopping") for s in counted):
        overall = "yellow"
    elif running_count == 0:
        overall = "grey"  # 服务管理器开着但什么都没运行
    else:
        overall = "green"
    return Summary(manager_running=processes is not None, overall=overall, running_count=running_count,
                   error_count=error_count, groups=groups, services=services)


def collect(reg: Registry | None = None, client=None) -> Summary:
    """读取清单并向 process-compose 查询当前状态。"""
    from servicedeck import registry
    from servicedeck.compose.client import ComposeClient, ComposeError

    reg = reg if reg is not None else registry.load()
    client = client or ComposeClient()
    try:
        processes = client.processes()
    except ComposeError:
        processes = None
    return summarize(reg, processes, load_user_stopped())
