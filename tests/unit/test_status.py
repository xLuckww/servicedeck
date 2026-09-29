import pytest

from servicedeck import status
from servicedeck.models import Group, Registry, Service


def svc(id, group="g", **kw):
    return Service(**dict(dict(id=id, name=id.upper(), group=group, template="custom", command="x"), **kw))


def reg(*services):
    return Registry(groups=[Group(id="g", name="G"), Group(id="h", name="H")], services=list(services))


def p(name, st="Running", ready="-", probe=False, exit_code=0, restarts=0, pid=100, age=5_000_000_000, running=None):
    if running is None:
        running = st in ("Running", "Launched", "Restarting")
    return dict(name=name, status=st, is_ready=ready, has_ready_probe=probe, exit_code=exit_code,
                restarts=restarts, pid=pid, age=age, is_running=running)


HEALTHY = p("x", "Running", ready="Ready", probe=True)


@pytest.mark.parametrize("proc,autostart,expected", [
    (p("x", "Running"), True, "healthy"),
    (p("x", "Running", ready="Ready", probe=True), True, "healthy"),
    (p("x", "Running", ready="Not Ready", probe=True), True, "starting"),
    (p("x", "Running", ready="-", probe=True), True, "starting"),
    (p("x", "Pending", running=False), True, "starting"),
    (p("x", "Launching", running=False), True, "starting"),
    (p("x", "Restarting"), True, "starting"),
    (p("x", "Terminating"), True, "stopping"),
    (p("x", "Completed", exit_code=-1, running=False), True, "stopped"),
    (p("x", "Completed", exit_code=0, running=False), True, "stopped"),
    (p("x", "Completed", exit_code=143, running=False), True, "stopped"),
    (p("x", "Completed", exit_code=3, restarts=3, running=False), True, "error"),
    (p("x", "Completed", exit_code=1, running=False), True, "error"),
    (p("x", "Completed", exit_code=-1, restarts=3, running=False), True, "error"),
    (p("x", "Skipped", exit_code=1, running=False), True, "error"),
    (p("x", "Error", running=False), True, "error"),
    (p("x", "Disabled", running=False), False, "manual"),
    (p("x", "Completed", exit_code=-1, running=False), False, "manual"),
    (p("x", "Running"), False, "healthy"),
    (p("x", "Completed", exit_code=2, restarts=3, running=False), False, "error"),
    (None, True, "stopped"),
    (None, False, "manual"),
])
def test_service_display(proc, autostart, expected):
    s = svc("x", autostart=autostart)
    assert status.service_status(s, proc).display == expected


def test_service_status_fields():
    s = svc("x", port=18300)
    st = status.service_status(s, p("x", "Running", pid=4321, age=65_000_000_000))
    assert st.color == "green" and st.label == "运行中"
    assert st.pid == 4321 and st.uptime_seconds == 65 and st.port == 18300
    assert status.service_status(svc("x", autostart=False), None).label == "手动开启"
    assert status.service_status(s, p("x", "Completed", exit_code=0, running=False)).pid is None


def test_error_reasons():
    s = svc("x")
    assert "退出码 3" in status.service_status(s, p("x", "Completed", exit_code=3, restarts=3, running=False)).reason
    assert "依赖" in status.service_status(s, p("x", "Skipped", running=False)).reason


def test_frp_stopped_overall_green():
    r = reg(svc("db"), svc("web"), svc("frpc", autostart=False))
    procs = [p("db"), p("web"), p("frpc", "Disabled", running=False)]
    sm = status.summarize(r, procs)
    assert sm.overall == "green"
    assert sm.running_count == 2 and sm.error_count == 0
    g = sm.group("g")
    assert (g.running, g.total) == (2, 2)  # 停着的 frp 不计入


def test_frp_running_counts():
    r = reg(svc("db"), svc("frpc", autostart=False))
    sm = status.summarize(r, [p("db"), p("frpc")])
    assert sm.overall == "green"
    assert (sm.group("g").running, sm.group("g").total) == (2, 2)


def test_frp_running_then_crashed_overall_red():
    r = reg(svc("db"), svc("frpc", autostart=False))
    sm = status.summarize(r, [p("db"), p("frpc", "Completed", exit_code=1, restarts=3, running=False)])
    assert sm.overall == "red"
    assert sm.error_count == 1
    assert (sm.group("g").running, sm.group("g").total) == (1, 2)


def test_manager_not_running_grey():
    r = reg(svc("db"), svc("frpc", autostart=False))
    sm = status.summarize(r, None)
    assert sm.overall == "grey"
    assert sm.manager_running is False
    assert all(s.display in ("stopped", "manual") for s in sm.services)


def test_starting_yellow_and_error_wins():
    r = reg(svc("a"), svc("b"), svc("c"))
    assert status.summarize(r, [p("a"), p("b", "Pending", running=False), p("c")]).overall == "yellow"
    assert status.summarize(r, [p("a"), p("b", "Pending", running=False), p("c", "Error", running=False)]).overall == "red"


def test_nothing_running_is_grey():
    r = reg(svc("a"), svc("frpc", autostart=False))
    sm = status.summarize(r, [p("a", "Completed", exit_code=-1, running=False), p("frpc", "Disabled", running=False)])
    assert sm.overall == "grey"


def test_one_autostart_stopped_others_healthy_is_green():
    r = reg(svc("a"), svc("b"))
    assert status.summarize(r, [p("a"), p("b", "Completed", exit_code=-1, running=False)]).overall == "green"


def test_groups_order_and_empty_group():
    r = reg(svc("a", group="h"), svc("b", group="g"))
    sm = status.summarize(r, [p("a"), p("b")])
    assert [g.id for g in sm.groups] == ["g", "h"]
    assert [s.id for s in sm.group("h").services] == ["a"]


def test_to_dict_is_json_serializable():
    import json
    r = reg(svc("a", port=1))
    json.dumps(status.summarize(r, [p("a")]).to_dict(), ensure_ascii=False)


def test_user_stopped_service_is_stopped_even_with_stale_restart_count():
    # Ollama 实例：之前外置盘没挂载失败重启过 3 次，后来启动成功，再被用户手动停止
    r = reg(svc("ollama"))
    proc = p("ollama", "Completed", exit_code=0, restarts=3, running=False)
    assert status.summarize(r, [proc]).service("ollama").display == "error"
    st = status.summarize(r, [proc], user_stopped={"ollama"}).service("ollama")
    assert st.display == "stopped" and st.reason is None


def test_user_stopped_does_not_hide_running_or_crash_after_restart():
    r = reg(svc("a"))
    assert status.summarize(r, [p("a")], user_stopped={"a"}).service("a").display == "healthy"


def test_user_stopped_file_roundtrip():
    assert status.load_user_stopped() == set()
    status.mark_user_stopped(["a", "b"])
    status.clear_user_stopped(["a"])
    assert status.load_user_stopped() == {"b"}
    status.clear_user_stopped()
    assert status.load_user_stopped() == set()
