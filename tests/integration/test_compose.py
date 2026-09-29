import os
import subprocess
import time

import pytest

from servicedeck import paths, registry
from servicedeck.compose import lifecycle
from servicedeck.compose.client import ComposeError

from .helpers import PY, http_service, plain_service, make_registry, port_open, state, wait_for

pytestmark = pytest.mark.integration


def ready(client, sid):
    s = client.process(sid)
    return s["is_running"] and (not s["has_ready_probe"] or s["is_ready"] == "Ready")


def test_start_query_stop_restart(pc):
    client = pc(make_registry(
        http_service("alpha", 18301),
        http_service("manual", 18302, autostart=False),
    ))
    assert client.is_alive()
    pc_pid = lifecycle.read_pid()
    assert pc_pid
    # 由 launchd 托管：父进程是 launchd（1），与启动它的程序无关
    ppid = int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pc_pid)], capture_output=True, text=True).stdout)
    assert ppid == 1
    assert lifecycle.launchd_loaded() and lifecycle.launchd_pid() == pc_pid
    wait_for(lambda: ready(client, "alpha"), desc="alpha 就绪")
    assert port_open(18301)
    names = {p["name"]: p for p in client.processes()}
    assert names["manual"]["status"] == "Disabled"

    # 启动手动服务
    client.start("manual")
    wait_for(lambda: ready(client, "manual"), desc="manual 就绪")

    # 停止
    client.stop("alpha")
    wait_for(lambda: not port_open(18301), desc="alpha 端口释放")
    assert not client.process("alpha")["is_running"]

    # 重启：PID 变化
    client.start("alpha")
    wait_for(lambda: ready(client, "alpha"), desc="alpha 再次就绪")
    pid1 = client.process("alpha")["pid"]
    client.restart("alpha")
    wait_for(lambda: ready(client, "alpha") and client.process("alpha")["pid"] != pid1, desc="alpha 重启")

    # 日志
    import urllib.request
    urllib.request.urlopen("http://127.0.0.1:18301/").read()
    wait_for(lambda: any("GET /" in line for line in client.logs("alpha", 20)), desc="日志里有请求记录")
    log_text = paths.service_log_file("alpha").read_text()
    assert "GET /" in log_text

    # 未知服务
    with pytest.raises(ComposeError):
        client.start("nope")


def test_ensure_running_attaches_instead_of_restarting(pc):
    client = pc(make_registry(http_service("alpha", 18303)))
    wait_for(lambda: ready(client, "alpha"))
    pid_pc = lifecycle.read_pid()
    pid_alpha = client.process("alpha")["pid"]
    again = lifecycle.ensure_running()
    assert lifecycle.read_pid() == pid_pc
    assert again.process("alpha")["pid"] == pid_alpha


def test_reload_only_restarts_changed(pc):
    client = pc(make_registry(
        http_service("alpha", 18304),
        http_service("beta", 18305),
        http_service("gamma", 18306),
    ))
    for sid in ("alpha", "beta", "gamma"):
        wait_for(lambda sid=sid: ready(client, sid), desc=f"{sid} 就绪")
    pids = {sid: client.process(sid)["pid"] for sid in ("alpha", "beta", "gamma")}

    old = registry.load()
    new = old.model_copy(deep=True)
    new.service("beta").env = {"CHANGED": "1"}
    new.services = [s for s in new.services if s.id != "gamma"] + [http_service("delta", 18307)]
    result = lifecycle.apply(new)
    assert result.changes == {"beta": "updated", "gamma": "removed", "delta": "added"}

    wait_for(lambda: ready(client, "beta") and client.process("beta")["pid"] != pids["beta"], desc="beta 重启")
    wait_for(lambda: ready(client, "delta"), desc="delta 启动")
    wait_for(lambda: not port_open(18306), desc="gamma 已停止")
    assert client.process("alpha")["pid"] == pids["alpha"]  # 没改动的服务不受影响
    assert {p["name"] for p in client.processes()} == {"alpha", "beta", "delta"}

    # 什么都不改再应用一次：没有任何变化
    assert lifecycle.apply(registry.load()).changes == {}
    assert client.process("alpha")["pid"] == pids["alpha"]


def test_edit_keeps_stopped_stopped_and_manual_running(pc):
    client = pc(make_registry(
        http_service("auto", 18308),
        http_service("manual", 18309, autostart=False),
    ))
    wait_for(lambda: ready(client, "auto"))
    client.stop("auto")
    client.start("manual")
    wait_for(lambda: ready(client, "manual"))

    new = registry.load()
    new.service("auto").env = {"X": "1"}
    new.service("manual").env = {"X": "1"}
    result = lifecycle.apply(new)
    assert result.changes == {"auto": "updated", "manual": "updated"}
    # 停着的仍然停着，开着的手动服务仍然开着（且已按新配置重启）
    wait_for(lambda: ready(client, "manual"), desc="manual 重新启动")
    wait_for(lambda: not client.process("auto")["is_running"] and not port_open(18308), desc="auto 保持停止")
    time.sleep(1.5)
    assert not client.process("auto")["is_running"]


def test_apply_when_manager_not_running(sd_home):
    result = lifecycle.apply(make_registry(http_service("alpha", 18310)))
    assert result.reloaded is False
    assert paths.compose_file().exists()
    assert registry.load().service("alpha")


def test_apply_rolls_back_on_reload_failure(pc, monkeypatch):
    client = pc(make_registry(http_service("alpha", 18311)))
    wait_for(lambda: ready(client, "alpha"))
    before_compose = paths.compose_file().read_text()
    before_registry = paths.registry_file().read_text()

    from servicedeck.compose import config
    monkeypatch.setattr(config, "render", lambda reg: "processes: [this is: not valid\n")
    new = registry.load()
    new.service("alpha").env = {"X": "1"}
    with pytest.raises(lifecycle.ApplyError):
        lifecycle.apply(new)
    assert paths.compose_file().read_text() == before_compose
    assert paths.registry_file().read_text() == before_registry
    assert ready(client, "alpha")


def test_dependency_order(pc):
    client = pc(make_registry(
        http_service("db", 18312, command=f"sleep 2; exec {PY} -m http.server 18312 --bind 127.0.0.1"),
        http_service("web", 18313, depends_on=["db"]),
    ))
    wait_for(lambda: ready(client, "web"), timeout=25, desc="web 就绪")
    db, web = client.process("db"), client.process("web")
    assert web["process_start_time"] >= db["process_ready_time"]


def test_crash_restarts_at_most_3_times(pc):
    client = pc(make_registry(plain_service("crashy", "echo boom; exit 3")))
    wait_for(lambda: client.process("crashy")["status"] == "Completed" and client.process("crashy")["restarts"] == 3,
             timeout=20, desc="重启 3 次后停止")
    time.sleep(1.5)
    s = client.process("crashy")
    assert s["restarts"] == 3 and s["exit_code"] == 3


def test_escaping_and_service_path(pc):
    client = pc(make_registry(plain_service(
        "echoer",
        'echo "home=${HOME} braces={{x}}"; echo "path=$PATH"; command -v uv; sleep 60',
    )))
    home = os.path.expanduser("~")
    wait_for(lambda: any(f"home={home} braces={{{{x}}}}" in l for l in client.logs("echoer", 20)), desc="转义后的输出")
    logs = client.logs("echoer", 20)
    assert any(l.startswith("path=") and "/opt/homebrew/bin" in l and ".local/share/mise/shims" in l for l in logs)
    assert any(l.strip().endswith("/uv") for l in logs)


def test_minimal_path_environment(pc, monkeypatch):
    # 模拟从访达打开 App：PATH 只有最基本的几项
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    client = pc(make_registry(
        http_service("alpha", 18314),
        plain_service("tool", "command -v uv npm; sleep 60"),
    ))
    wait_for(lambda: ready(client, "alpha"), desc="最小 PATH 下 alpha 就绪")
    wait_for(lambda: len([l for l in client.logs("tool", 10) if l.startswith("/")]) >= 2, desc="服务里能找到 uv 和 npm")


def test_shutdown_stops_everything(pc):
    client = pc(make_registry(http_service("alpha", 18315)))
    wait_for(lambda: ready(client, "alpha"))
    pid = lifecycle.read_pid()
    lifecycle.shutdown(timeout=20)
    assert not client.is_alive()
    assert not port_open(18315)
    assert lifecycle.read_pid() is None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not lifecycle.launchd_loaded()  # launchd 里的登记也已注销
