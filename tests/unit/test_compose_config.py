import os
import stat
from pathlib import Path

import pytest
import yaml

from servicedeck import envpath, paths
from servicedeck.compose import config
from servicedeck.models import Group, Registry, Service

HOME = str(Path.home())


def svc(id, **kw):
    base = dict(id=id, name=id.upper(), group="g", template="custom", command="echo hi")
    base.update(kw)
    return Service(**base)


def reg(*services):
    return Registry(groups=[Group(id="g", name="G")], services=list(services))


def proc(r, sid):
    return config.to_compose(r)["processes"][sid]


def env_of(p):
    return dict(item.split("=", 1) for item in p["environment"])


def test_top_level(sd_home):
    c = config.to_compose(reg(svc("a")))
    assert c["version"] == "0.5"
    assert c["log_location"] == str(sd_home / "logs" / "process-compose.log")
    assert list(c["processes"]) == ["a"]


def test_basic_process(sd_home):
    p = proc(reg(svc("a")), "a")
    assert p["command"] == "echo hi"
    assert "description" not in p  # 改名不应导致重启
    assert p["disabled"] is False
    assert p["disable_dotenv"] is True
    assert p["log_location"] == str(sd_home / "logs" / "a.log")
    assert p["log_configuration"]["rotation"] == {"max_size_mb": 10, "max_backups": 3}
    assert p["availability"] == {"restart": "on_failure", "max_restarts": 3}
    assert p["shutdown"] == {"signal": 15, "timeout_seconds": 30}
    assert "readiness_probe" not in p and "depends_on" not in p and "working_dir" not in p


def test_path_injected_and_env_merged():
    p = proc(reg(svc("a", env={"LC_ALL": "en_US.UTF-8"})), "a")
    env = env_of(p)
    assert env["PATH"] == envpath.service_path()
    assert env["LC_ALL"] == "en_US.UTF-8"


def test_user_path_overrides():
    env = env_of(proc(reg(svc("a", env={"PATH": "/custom/bin:/usr/bin"})), "a"))
    assert env["PATH"] == "/custom/bin:/usr/bin"


def test_autostart_false_is_disabled():
    assert proc(reg(svc("a", autostart=False)), "a")["disabled"] is True


def test_restart_policies():
    assert proc(reg(svc("a", restart="always")), "a")["availability"] == {"restart": "always", "max_restarts": 3}
    assert proc(reg(svc("a", restart="no")), "a")["availability"] == {"restart": "no"}


def test_shutdown_signal():
    assert proc(reg(svc("a", shutdown_signal=2)), "a")["shutdown"]["signal"] == 2


def test_port_without_health_uses_nc():
    p = proc(reg(svc("a", port=18300)), "a")
    probe = p["readiness_probe"]
    assert probe["exec"]["command"] == "/usr/bin/nc -z 127.0.0.1 18300"
    assert probe["initial_delay_seconds"] == 1
    assert probe["period_seconds"] == 2
    assert probe["failure_threshold"] == 30


def test_http_health():
    p = proc(reg(svc("a", port=3000, health={"type": "http", "path": "/health", "initial_delay": 5, "timeout": 300})), "a")
    probe = p["readiness_probe"]
    assert probe["http_get"] == {"host": "127.0.0.1", "port": 3000, "path": "/health", "scheme": "http"}
    assert probe["initial_delay_seconds"] == 5
    assert probe["period_seconds"] == 5
    assert probe["failure_threshold"] == 60
    assert probe["timeout_seconds"] >= 1


def test_exec_health():
    p = proc(reg(svc("a", health={"type": "exec", "command": "pg_isready -q"})), "a")
    assert p["readiness_probe"]["exec"]["command"] == "pg_isready -q"
    assert p["readiness_probe"]["failure_threshold"] == 12  # 60 秒 / 5 秒


def test_depends_on_conditions():
    r = reg(svc("db", port=18300), svc("worker"), svc("web", depends_on=["db", "worker"]))
    assert proc(r, "web")["depends_on"] == {
        "db": {"condition": "process_healthy"},
        "worker": {"condition": "process_started"},
    }


def test_tilde_expanded(tmp_path):
    p = proc(reg(svc("a", cwd="~", command="~/frp/frpc -c ~/frp/frpc.toml", env_file="~/x.env")), "a")
    assert p["working_dir"] == HOME
    assert p["command"] == f"{HOME}/frp/frpc -c {HOME}/frp/frpc.toml"
    assert p["env_file"] == f"{HOME}/x.env"


def test_relative_env_file_against_cwd(tmp_path):
    p = proc(reg(svc("a", cwd=str(tmp_path), env_file=".env")), "a")
    assert p["env_file"] == str(tmp_path / ".env")


def test_env_file_is_referenced_not_inlined(tmp_path):
    (tmp_path / ".env").write_text("SECRET=abc\n")
    r = reg(svc("a", cwd=str(tmp_path), env_file=".env"))
    assert "abc" not in config.render(r)


def test_dollar_and_template_escaped():
    # process-compose 会自己展开 ${X} 和 {{ }}，写进配置前要转义
    p = proc(reg(svc("a", command='echo "${HOME} $X {{ .Y }}"', env={"PW": "p$ss"}, health={"type": "exec", "command": "test $A"})), "a")
    assert p["command"] == 'echo "$${HOME} $$X {{"{{"}} .Y }}"'
    assert env_of(p)["PW"] == "p$$ss"
    assert p["readiness_probe"]["exec"]["command"] == "test $$A"


def test_write_file(sd_home):
    config.write(reg(svc("a")))
    f = paths.compose_file()
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    data = yaml.safe_load(f.read_text())
    assert "a" in data["processes"]
    assert "不要手改" in f.read_text()


def test_empty_registry_has_processes_key():
    assert config.to_compose(Registry())["processes"] == {}


def test_rename_or_regroup_does_not_change_process():
    a = proc(reg(svc("a")), "a")
    assert proc(reg(svc("a", name="新名字", start_warning="小心", template_fields={"x": 1})), "a") == a
