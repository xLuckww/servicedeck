import os
import signal
import subprocess
import time

import pytest

from servicedeck import actions, paths, registry
from servicedeck.compose import lifecycle
from servicedeck.compose.client import ComposeClient

from .helpers import PY, http_service, make_registry, plain_service, port_open, wait_for

pytestmark = pytest.mark.integration


def ready(sid):
    try:
        s = ComposeClient().process(sid)
    except Exception:
        return False
    return s["is_running"] and (not s["has_ready_probe"] or s["is_ready"] == "Ready")


def pid_of(sid):
    return ComposeClient().process(sid)["pid"]


@pytest.fixture
def terminal_server(tmp_path):
    procs = []

    def _start(port, cwd=None):
        p = subprocess.Popen([PY, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=cwd or tmp_path,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(p)
        wait_for(lambda: port_open(port), desc=f"终端服务 {port}")
        return p

    yield _start
    for p in procs:
        if p.poll() is None:
            p.kill()
            p.wait()


def test_start_stop_restart_and_dependents(pc):
    pc(make_registry(
        http_service("db", 18370),
        http_service("web", 18371, depends_on=["db"]),
    ))
    wait_for(lambda: ready("db") and ready("web"), timeout=20, desc="db、web 就绪")

    # 停止被依赖的服务：需要确认，列出受影响的服务
    r = actions.stop("db")
    assert r.needs_confirm and r.affected == ["web"]
    assert ready("db")
    assert actions.stop("web").ok
    wait_for(lambda: not port_open(18371))
    assert actions.stop("db").ok  # web 已停，不再需要确认
    wait_for(lambda: not port_open(18370))

    # 启动 web：会先把依赖的 db 启动起来
    assert actions.start("web").ok
    wait_for(lambda: ready("db") and ready("web"), timeout=20, desc="依赖一起启动")

    # 已在运行
    assert "已在运行" in actions.start("web").message

    # 重启
    old = pid_of("web")
    assert actions.restart("web").ok
    wait_for(lambda: ready("web") and pid_of("web") != old, desc="web 重启")


def test_start_blocked_by_port_owner(pc, terminal_server):
    pc(make_registry(http_service("svc", 18372, autostart=False)))
    squatter = terminal_server(18372)
    r = actions.start("svc")
    assert not r.ok and "18372" in r.message and str(squatter.pid) in r.message
    assert r.data["occupant"]["pid"] == squatter.pid
    assert r.data["occupant"]["unmanaged"] is True
    # 界面据此提供"停止它"
    r = actions.stop_unmanaged(squatter.pid)
    assert r.needs_confirm
    assert actions.stop_unmanaged(squatter.pid, confirm=True).ok
    wait_for(lambda: squatter.poll() is not None, desc="占用者退出")
    assert actions.start("svc", confirm=True).ok
    wait_for(lambda: ready("svc"))


def test_start_warning_then_start(pc):
    pc(make_registry(http_service("frp", 18373, autostart=False, start_warning="开启后本机服务可从公网访问")))
    r = actions.start("frp")
    assert r.needs_confirm and "公网" in r.prompt
    assert not port_open(18373)
    assert actions.start("frp", confirm=True).ok
    wait_for(lambda: ready("frp"))


def test_start_all_and_stop_all(pc):
    pc(make_registry(http_service("a", 18374), http_service("m", 18375, autostart=False)))
    wait_for(lambda: ready("a"))
    actions.stop("a")
    wait_for(lambda: not port_open(18374))
    assert actions.start_all().ok
    wait_for(lambda: ready("a"), desc="全部启动补启动 a")
    assert not port_open(18375)  # 手动服务不跟随
    assert actions.stop_all().ok
    assert not ComposeClient().is_alive()
    assert not port_open(18374)
    # 管理器未运行时"全部启动"会把它启动起来
    assert actions.start_all().ok
    wait_for(lambda: ready("a"), desc="重新全部启动")


def test_start_when_manager_down_with_confirm(pc):
    pc(make_registry(http_service("a", 18376), http_service("m", 18377, autostart=False)))
    wait_for(lambda: ready("a"))
    actions.stop_all()
    r = actions.start("m")
    assert r.needs_confirm and "服务管理器未运行" in r.prompt
    assert actions.start("m", confirm=True).ok
    wait_for(lambda: ready("a") and ready("m"), desc="管理器和 m 都启动")


def test_edit_running_service_confirms_restart_only_that_one(pc):
    pc(make_registry(http_service("a", 18378), http_service("b", 18379)))
    wait_for(lambda: ready("a") and ready("b"))
    pid_a, pid_b = pid_of("a"), pid_of("b")

    d = actions.draft_for_edit("b").data["draft"]
    d["env"] = {"X": "1"}
    r = actions.save_service(d, service_id="b")
    assert r.needs_confirm and r.affected == ["b"] and "将重启" in r.prompt
    assert pid_of("b") == pid_b  # 未确认前不生效

    r = actions.save_service(d, service_id="b", confirm=True)
    assert r.ok and r.data["changes"] == {"b": "updated"}
    wait_for(lambda: ready("b") and pid_of("b") != pid_b, desc="b 重启")
    assert pid_of("a") == pid_a

    # 只改名字：不需要重启、不需要确认
    d = actions.draft_for_edit("a").data["draft"]
    d["name"] = "新名字"
    r = actions.save_service(d, service_id="a")
    assert r.ok and r.data["changes"] == {}
    assert pid_of("a") == pid_a


def test_add_and_delete_running(pc):
    pc(make_registry(http_service("a", 18380)))
    wait_for(lambda: ready("a"))
    r = actions.save_service({
        "name": "new svc", "group": "test", "template": "custom",
        "template_fields": {"command": f"exec {PY} -m http.server 18381 --bind 127.0.0.1"}, "port": 18381,
    })
    assert r.ok and r.data["changes"] == {"new-svc": "added"}
    wait_for(lambda: ready("new-svc"), desc="新服务启动")

    r = actions.delete_service("new-svc")
    assert r.needs_confirm and "正在运行" in r.prompt
    assert actions.delete_service("new-svc", confirm=True).ok
    wait_for(lambda: not port_open(18381), desc="删除后停止")


def test_save_blocked_by_port_owner(pc, terminal_server):
    pc(make_registry(http_service("a", 18382)))
    wait_for(lambda: ready("a"))
    terminal_server(18383)
    r = actions.save_service({"name": "x", "group": "test", "template": "custom",
                              "template_fields": {"command": "sleep 1"}, "port": 18383})
    assert not r.ok and "port" in {e["field"] for e in r.errors}
    # 自己占用的端口不算冲突：编辑正在运行的 a，端口不变
    d = actions.draft_for_edit("a").data["draft"]
    d["name"] = "A2"
    assert actions.save_service(d, service_id="a").ok


def test_trial_run_cleans_up(sd_home):
    before = paths.registry_file().read_text() if paths.registry_file().exists() else None
    d = {"name": "trial", "group": "test", "template": "custom",
         "template_fields": {"command": f"echo 试运行开始; exec {PY} -u -m http.server 18384 --bind 127.0.0.1"},
         "port": 18384}
    r = actions.trial_run(d, seconds=4)
    assert r.ok, r.message
    snap = r.data
    assert snap["port_up"] is True
    assert any("试运行开始" in l for l in snap["logs"])
    assert snap["finished"] is True
    assert not port_open(18384)
    assert not os.path.exists(snap["workdir"])  # 临时实例的目录已删除
    after = paths.registry_file().read_text() if paths.registry_file().exists() else None
    assert before == after  # 不写入清单


def test_trial_async_api_and_stop(sd_home):
    d = {"name": "trial", "group": "test", "template": "custom",
         "template_fields": {"command": "echo hello; sleep 30"}}
    r = actions.start_trial(d, seconds=15)
    assert r.ok
    tid = r.data["trial_id"]
    wait_for(lambda: any("hello" in l for l in actions.trial_status(tid).data["logs"]), desc="试运行日志")
    s = actions.trial_status(tid).data
    assert s["running"] and not s["finished"] and s["port_up"] is None
    assert actions.stop_trial(tid).ok
    s = actions.trial_status(tid).data
    assert s["finished"]


def test_trial_crash_reports_exit(sd_home):
    d = {"name": "t", "group": "test", "template": "custom", "template_fields": {"command": "echo oops; exit 4"}}
    snap = actions.trial_run(d, seconds=3).data
    assert snap["finished"] and snap["exit_code"] == 4
    assert any("oops" in l for l in snap["logs"])


def test_trial_port_conflict(sd_home, terminal_server):
    terminal_server(18385)
    d = {"name": "t", "group": "test", "template": "custom", "template_fields": {"command": "sleep 5"}, "port": 18385}
    r = actions.start_trial(d)
    assert not r.ok and "18385" in r.message


def test_list_and_adopt_unmanaged(pc, terminal_server, tmp_path):
    pc(make_registry(http_service("a", 18386)))
    wait_for(lambda: ready("a"))
    folder = tmp_path / "proj"
    folder.mkdir()
    squatter = terminal_server(18387, cwd=folder)

    items = actions.list_unmanaged().data["items"]
    mine = [i for i in items if i["pid"] == squatter.pid]
    assert mine and mine[0]["ports"] == [18387]
    assert 18386 not in {p for i in items for p in i["ports"]}

    r = actions.adopt(squatter.pid)
    assert r.ok
    d = r.data["draft"]
    assert d["template"] == "custom" and d["port"] == 18387 and d["adopt_pid"] == squatter.pid
    assert "http.server" in d["template_fields"]["command"]
    assert os.path.realpath(d["template_fields"]["folder"]) == os.path.realpath(folder)
    assert any("环境变量" in h for h in r.data["hints"])

    d["name"] = "adopted"
    d["group"] = "test"
    r = actions.save_service(d)
    assert r.needs_confirm and str(squatter.pid) in r.prompt
    r = actions.save_service(d, confirm=True)
    assert r.ok, r.message
    wait_for(lambda: squatter.poll() is not None, desc="原进程被停止")
    wait_for(lambda: ready("adopted"), desc="由 ServiceDeck 启动")
    assert squatter.pid not in {i["pid"] for i in actions.list_unmanaged().data["items"]}


def test_adopt_rejects_managed_pid(pc):
    pc(make_registry(http_service("a", 18388)))
    wait_for(lambda: ready("a"))
    assert not actions.adopt(pid_of("a")).ok
    r = actions.stop_unmanaged(pid_of("a"), confirm=True)
    assert not r.ok and "ServiceDeck" in r.message


def test_stop_unmanaged_force(sd_home, tmp_path):
    # 忽略 SIGTERM 的进程：5 秒后询问是否强制结束
    script = tmp_path / "stubborn.py"
    script.write_text(
        "import signal, socket, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "s.bind(('127.0.0.1', 18389)); s.listen()\n"
        "time.sleep(60)\n"
    )
    p = subprocess.Popen([PY, str(script)], cwd=tmp_path)
    try:
        wait_for(lambda: port_open(18389))
        r = actions.stop_unmanaged(p.pid, confirm=True)
        assert r.needs_confirm and r.confirm_kind == "force_kill"
        assert p.poll() is None
        assert actions.stop_unmanaged(p.pid, confirm=True, force=True).ok
        wait_for(lambda: p.poll() is not None)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


def test_service_logs(pc):
    pc(make_registry(plain_service("echoer", "echo 第一行; echo 第二行; sleep 60")))
    wait_for(lambda: "第二行" in "".join(actions.service_logs("echoer").data["lines"]))
    lifecycle.shutdown()
    # 管理器停了也能从日志文件读到
    assert any("第二行" in l for l in actions.service_logs("echoer").data["lines"])


def test_trial_without_name(sd_home):
    r = actions.trial_run({"template": "custom", "template_fields": {"command": "echo hi"}}, seconds=2)
    assert r.ok and r.data["finished"]


def test_manual_stop_is_remembered_and_cleared_on_start(pc):
    from servicedeck import status
    pc(make_registry(http_service("a", 18365)))
    wait_for(lambda: ready("a"))
    assert actions.stop("a").ok
    wait_for(lambda: not port_open(18365))
    assert "a" in status.load_user_stopped()
    assert status.collect().service("a").display == "stopped"
    assert actions.start("a").ok
    assert "a" not in status.load_user_stopped()
    wait_for(lambda: ready("a"))
