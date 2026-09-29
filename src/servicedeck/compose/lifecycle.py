"""启动 / 接管 / 停止 process-compose 本身，以及"应用新清单"（设计 5.3、9）。

正式的 process-compose 交给 macOS 的 launchd 托管运行（2026-09-28，设计决策 S16）：它和 ServiceDeck.app、
命令行、Claude 都没有父子关系，App 退出、崩溃或被强制结束都不会牵连服务。登记是临时的（配置文件放在 run/，
不在 ~/Library/LaunchAgents），开机后仍由 App 按"开机自动启动服务"的设置拉起。PID 记在 run/pc.pid。
试运行的临时实例仍直接作为子进程启动（用完即停）。
没有用 `up -D`：它会再分出一个子进程，拿不到真正的 PID，而"未纳入管理"的判定需要这个 PID。
"""

import hashlib
import plistlib

import os
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field

from servicedeck import envpath, paths, registry
from servicedeck.compose import config
from servicedeck.compose.client import ComposeClient, ComposeError
from servicedeck.models import Registry


class LifecycleError(Exception):
    pass


class ApplyError(Exception):
    pass


@dataclass
class ApplyResult:
    reloaded: bool
    changes: dict[str, str] = field(default_factory=dict)


def pc_binary() -> str:
    override = os.environ.get("SERVICEDECK_PC_BIN")
    if override:
        return override
    try:
        mise = envpath.resolve_tool("mise")
        out = subprocess.run([mise, "which", "process-compose"], capture_output=True, text=True, timeout=15,
                             env=envpath.service_env(), cwd=os.path.expanduser("~"))
        path = out.stdout.strip()
        if out.returncode == 0 and path and os.access(path, os.X_OK):
            return path
    except (envpath.ToolNotFound, subprocess.TimeoutExpired, OSError):
        pass
    try:
        return envpath.resolve_tool("process-compose")
    except envpath.ToolNotFound as e:
        raise LifecycleError("找不到 process-compose，请先用 mise 安装它") from e


def read_pid() -> int | None:
    f = paths.pc_pid_file()
    try:
        pid = int(f.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    return pid


def _reap(proc: subprocess.Popen) -> None:
    # App 里 process-compose 退出后回收它，避免留下僵尸进程
    threading.Thread(target=proc.wait, daemon=True).start()


def ensure_running(timeout: float = 20) -> ComposeClient:
    """socket 可用就直接接上；否则按当前清单生成配置，交给 launchd 启动 process-compose（会启动所有自动启动的服务）。"""
    client = ComposeClient()
    if client.is_alive():
        return client

    sock = paths.socket_path()
    paths.check_socket_path(sock)
    paths.ensure_dirs()
    config.write(registry.load())
    sock.unlink(missing_ok=True)  # 上次异常退出留下的旧 socket
    pid = _launchd_start(pc_command(paths.compose_file(), sock, paths.logs_dir()))
    paths.pc_pid_file().write_text(str(pid))
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.is_alive():
            return client
        if not _alive(pid):
            break
        time.sleep(0.1)
    _launchd_stop()
    paths.pc_pid_file().unlink(missing_ok=True)
    raise LifecycleError(f"服务管理器没能启动。最后的输出：\n{_log_tail(paths.logs_dir())}")


def pc_command(compose_file, sock, log_dir) -> list[str]:
    return [
        pc_binary(), "up", "-t=false",
        "-f", str(compose_file),
        "-U", "-u", str(sock),
        "--disable-dotenv", "--keep-project", "--ordered-shutdown",
        "-L", str(log_dir / "process-compose.log"),
    ]


# ---------- launchd 托管 ----------

def launchd_label() -> str:
    """正式实例固定叫 local.servicedeck.pc；测试（设置了 SERVICEDECK_HOME）按目录生成不同的名字，绝不碰正式实例。"""
    if os.environ.get("SERVICEDECK_HOME"):
        digest = hashlib.sha1(str(paths.home()).encode()).hexdigest()[:10]
        return f"local.servicedeck.pc.test-{digest}"
    return "local.servicedeck.pc"


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True)


def _launchd_start(cmd: list[str]) -> int:
    label = launchd_label()
    plist = paths.run_dir() / f"{label}.plist"
    plist.write_bytes(plistlib.dumps({
        "Label": label,
        "ProgramArguments": cmd,
        "EnvironmentVariables": envpath.service_env(),
        "WorkingDirectory": str(paths.run_dir()),
        # 无界面模式下它会把所有服务的输出再打印一遍，每个服务已有自己的（会轮转的）日志，所以丢弃
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
        "RunAtLoad": True,
        "KeepAlive": False,  # 崩溃不自动拉起：由用户在 App 里"启动全部服务"
        "AbandonProcessGroup": True,
        "ProcessType": "Standard",
    }))
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{label}")  # 上次退出后仍登记着的旧任务
    r = _launchctl("bootstrap", domain, str(plist))
    if r.returncode != 0:
        raise LifecycleError(f"无法让系统启动服务管理器（launchctl bootstrap 失败）：{r.stderr.strip() or r.stdout.strip()}")
    deadline = time.time() + 10
    while time.time() < deadline:
        pid = launchd_pid(label)
        if pid:
            return pid
        time.sleep(0.1)
    raise LifecycleError(f"服务管理器没能启动。最后的输出：\n{_log_tail(paths.logs_dir())}")


def launchd_pid(label: str | None = None) -> int | None:
    out = _launchctl("list", label or launchd_label()).stdout
    m = re.search(r'"PID" = (\d+);', out)
    return int(m.group(1)) if m else None


def launchd_loaded(label: str | None = None) -> bool:
    return _launchctl("list", label or launchd_label()).returncode == 0


def _launchd_stop() -> None:
    _launchctl("bootout", f"gui/{os.getuid()}/{launchd_label()}")


def _log_tail(log_dir) -> str:
    log = log_dir / "process-compose.log"
    return log.read_text(errors="replace")[-1500:] if log.exists() else ""


# ---------- 试运行用的临时实例 ----------

def spawn(compose_file, sock, log_dir, cwd) -> subprocess.Popen:
    """作为子进程在新会话里启动一个临时 process-compose 实例（试运行用，用完即停）。"""
    paths.check_socket_path(sock)
    sock.unlink(missing_ok=True)
    proc = subprocess.Popen(pc_command(compose_file, sock, log_dir), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=envpath.service_env(), cwd=str(cwd), start_new_session=True)
    _reap(proc)
    return proc


def wait_alive(proc: subprocess.Popen, client: ComposeClient, log_dir, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.is_alive():
            return
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    if proc.poll() is None:
        proc.kill()
    raise LifecycleError(f"服务管理器没能启动。最后的输出：\n{_log_tail(log_dir)}")


def start_all() -> ComposeClient:
    """全部启动：服务管理器未运行就启动它（会启动所有自动启动的服务）；已运行就补启动还没运行的自动启动服务。"""
    client = ComposeClient()
    if not client.is_alive():
        return ensure_running()
    reg = registry.load()
    running = {p["name"] for p in client.processes() if p.get("is_running")}
    for s in reg.services:
        if s.autostart and s.id not in running:
            client.start(s.id)
    return client


def shutdown(timeout: float = 60) -> None:
    """停止全部服务和 process-compose 本身。"""
    client = ComposeClient()
    pid = read_pid()
    if client.is_alive():
        try:
            client.stop_project()
        except ComposeError:
            pass  # 服务管理器退出时连接会被断开，属正常
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not client.is_alive() and (pid is None or not _alive(pid)):
            break
        time.sleep(0.2)
    else:
        if pid and _alive(pid):
            os.kill(pid, signal.SIGTERM)
            time.sleep(2)
            if _alive(pid):
                os.kill(pid, signal.SIGKILL)
    _launchd_stop()  # 注销 launchd 里的登记（不存在时忽略）
    paths.pc_pid_file().unlink(missing_ok=True)
    paths.socket_path().unlink(missing_ok=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # 已退出但还没被回收的僵尸进程也算不在了
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


def apply(new: Registry) -> ApplyResult:
    """保存清单 → 生成配置 → 让 process-compose 重新加载（只重启有变化的服务）。

    失败时把清单和配置文件都回滚到上一版（process-compose 自己会保留旧配置继续运行）。
    额外处理两种情况（2026-09-28 实测）：重新加载会把"改动过的已停止服务"自动启动、
    把"改动过的正在运行的手动服务"停掉——这里恢复成改动前的运行状态。
    """
    client = ComposeClient()
    alive = client.is_alive()
    was_running: dict[str, bool] = {}
    if alive:
        was_running = {p["name"]: bool(p.get("is_running")) for p in client.processes()}

    reg_file, compose_file = paths.registry_file(), paths.compose_file()
    old_reg_text = reg_file.read_text() if reg_file.exists() else None
    old_compose_text = compose_file.read_text() if compose_file.exists() else None

    registry.save(new)
    try:
        config.write(new)
        if not alive:
            return ApplyResult(reloaded=False)
        changes = client.reload()
    except Exception as e:
        _restore(reg_file, old_reg_text)
        _restore(compose_file, old_compose_text)
        raise ApplyError(f"配置没有生效，已恢复到修改前：{e}") from e

    for sid, change in changes.items():
        if change != "updated" or sid not in was_running:
            continue
        svc = new.service(sid)
        try:
            if was_running[sid] and svc and not svc.autostart:
                client.start(sid)
            elif not was_running[sid] and svc and svc.autostart:
                client.stop(sid)
        except ComposeError:
            pass
    return ApplyResult(reloaded=True, changes=changes)


def _restore(path, text: str | None) -> None:
    if text is None:
        path.unlink(missing_ok=True)
    else:
        registry._atomic_write(path, text)
